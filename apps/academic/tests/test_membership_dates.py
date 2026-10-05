"""Former members (left_at) are not current anywhere, and their history stays visible."""
import datetime
import io
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.academic.models import Classroom, StudentClassroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.services import finalize_session
from apps.attendance.tests.helpers import (
    check_in, client_for, make_admin, make_course_info, make_semester, make_student, make_teacher, start_session,
)
from apps.faces.services import FaceError, recognize_class, save_face_attendance

D1, D2, D3 = (datetime.date(2026, 9, d) for d in (1, 2, 3))


class MembershipTestCase(TestCase):
    """`stays` is a current member; `left` was removed on D2; `late` joined on D2."""

    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        self.teacher = make_teacher('t@example.com')
        self.stays = make_student('stays@example.com', 2302001)
        self.left = make_student('left@example.com', 2302002)
        self.late = make_student('late@example.com', 2302003)
        self.semester = make_semester(students=[self.stays, self.left, self.late])
        StudentClassroom.objects.filter(student=self.left).update(left_at=D2)
        StudentClassroom.objects.filter(student=self.late).update(joined_at=D2)
        self.ci = make_course_info(self.teacher, semester=self.semester)
        self.teacher_client = client_for(self.teacher.user)


class LiveAttendanceTests(MembershipTestCase):
    def start(self):
        res = start_session(self.teacher_client, self.ci)
        self.assertEqual(res.status_code, 201)
        return res.data['session']

    def test_former_member_cannot_check_in_or_see_the_session(self):
        session = self.start()
        res = check_in(self.left, session['id'])
        self.assertEqual(res.status_code, 403)
        res = client_for(self.left.user).get('/api/student/live/')
        self.assertEqual(res.data['sessions'], [])
        res = client_for(self.stays.user).get('/api/student/live/')
        self.assertEqual([s['session_id'] for s in res.data['sessions']], [session['id']])

    def test_teacher_cannot_mark_a_former_member(self):
        session = self.start()
        res = self.teacher_client.post(f'/api/sessions/{session["id"]}/mark/', {
            'profile_id': str(self.left.id),
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_saved_session_logs_only_members_on_its_date(self):
        session = AttendanceSession.objects.create(course_info=self.ci, date=D1, mode='QR_ONLINE')
        finalize_session(session)
        self.assertEqual(
            set(AttendanceLog.objects.filter(session=session).values_list('student_id', flat=True)),
            {self.stays.id, self.left.id},  # left was still a member on D1; late joined on D2
        )
        session = AttendanceSession.objects.create(course_info=self.ci, date=D2, mode='QR_ONLINE')
        finalize_session(session)
        self.assertEqual(
            set(AttendanceLog.objects.filter(session=session).values_list('student_id', flat=True)),
            {self.stays.id, self.late.id},
        )


class TeacherViewTests(MembershipTestCase):
    def test_course_detail_lists_current_members_of_an_active_semester(self):
        res = self.teacher_client.get(f'/api/teacher/course-info/{self.ci.id}/')
        ids = [s['student_id'] for s in res.data['students']]
        self.assertEqual(ids, [2302001, 2302003])

    def test_finished_semester_keeps_everyone(self):
        self.semester.is_active = False
        self.semester.save()
        StudentClassroom.objects.update(left_at=self.today)  # promoted
        res = self.teacher_client.get(f'/api/teacher/course-info/{self.ci.id}/')
        ids = [s['student_id'] for s in res.data['students']]
        self.assertEqual(ids, [2302001, 2302002, 2302003])

    def test_roll_call_for_a_date_uses_the_members_on_that_date(self):
        res = self.teacher_client.post(f'/api/teacher/course-info/{self.ci.id}/roll-call/', {
            'date': '2026-09-01', 'present_profile_ids': [str(self.stays.id), str(self.late.id)],
        }, format='json')
        self.assertEqual(res.status_code, 200)
        logs = dict(AttendanceLog.objects.filter(date=D1).values_list('student__student_id', 'status'))
        self.assertEqual(logs, {2302001: 'PRESENT', 2302002: 'ABSENT'})  # not late (joined on D2)

    def test_csv_export_lists_the_class_list(self):
        res = self.teacher_client.get(f'/api/reports/course-info/{self.ci.id}/export/?export_format=csv')
        self.assertEqual(res.status_code, 200)
        text = res.content.decode()
        self.assertIn('2302001', text)
        self.assertIn('2302003', text)
        self.assertNotIn('2302002', text)


class AdminAndStudentViewTests(MembershipTestCase):
    def test_admin_students_row_shows_only_the_current_semester(self):
        admin = client_for(make_admin('admin@example.com'))
        rows = {r['student_id']: r for r in admin.get('/api/admin/students/').data['students']}
        self.assertEqual(rows[2302001]['semester']['id'], str(self.semester.id))
        self.assertIsNone(rows[2302002]['semester'])

    def test_student_summary_keeps_history_inside_the_membership(self):
        for day, status in ((D1, 'PRESENT'), (D2, 'ABSENT'), (D3, 'ABSENT')):
            AttendanceLog.objects.create(course_info=self.ci, student=self.left, date=day, status=status)
        res = client_for(self.left.user).get(f'/api/student/{self.left.id}/semesters/')
        self.assertEqual(res.status_code, 200)
        [semester] = res.data['semesters']
        self.assertEqual((semester['id'], semester['joined_at'], semester['left_at']),
                         (str(self.semester.id), None, '2026-09-02'))
        [course] = semester['courses']
        self.assertEqual((course['totalClasses'], course['presentCount'], course['percentage']), (1, 1, 100.0))
        self.assertEqual(course['history'], [{'date': '2026-09-01', 'present': True}])

    def test_student_summary_shows_only_their_class_groups_courses(self):
        other_group = Classroom.objects.create(name='B', semester=self.semester)
        other = make_course_info(self.teacher, 'CSE399', semester=self.semester)
        other.classroom = other_group
        other.save()
        res = client_for(self.stays.user).get(f'/api/student/{self.stays.id}/semesters/')
        [semester] = res.data['semesters']
        self.assertEqual([c['course']['code'] for c in semester['courses']], ['CSE301'])
        self.assertIsNone(semester['left_at'])


class FaceTests(MembershipTestCase):
    def test_recognize_compares_only_current_members(self):
        engine = mock.Mock(analyze=mock.Mock(return_value=[]))
        image = io.BytesIO()
        from PIL import Image
        Image.new('RGB', (50, 50)).save(image, format='PNG')
        upload = SimpleUploadedFile('class.png', image.getvalue(), content_type='image/png')
        with mock.patch('apps.faces.services.get_engine', return_value=engine):
            result = recognize_class(self.ci, [upload])
        self.assertEqual([r['student_id'] for r in result['students']], [2302001, 2302003])

    def test_saving_rejects_students_not_enrolled_that_day(self):
        with self.assertRaises(FaceError):
            save_face_attendance(self.ci, [self.left.id], day=self.today)
        with self.assertRaises(FaceError):
            save_face_attendance(self.ci, [self.late.id], day=D1)
        session = save_face_attendance(self.ci, [self.left.id], day=D1)
        self.assertEqual(
            dict(session.logs.values_list('student__student_id', 'status')),
            {2302001: 'ABSENT', 2302002: 'PRESENT'},
        )
