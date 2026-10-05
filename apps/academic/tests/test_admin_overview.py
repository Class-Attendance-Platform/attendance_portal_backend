"""Admin overview and the taught-courses list with attendance numbers (API v2 section 4)."""
import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academic.models import Course, CourseInfo, StudentClassroom
from apps.academic.stats import course_numbers
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.tests.helpers import (
    client_for, make_admin, make_course_info, make_semester, make_student, make_teacher,
)

D1, D2, D3, D4 = (datetime.date(2026, 9, d) for d in (1, 2, 3, 4))


def log(ci, student, day, status='PRESENT', session=None):
    return AttendanceLog.objects.create(course_info=ci, student=student, date=day, status=status, session=session)


class NumbersTestCase(TestCase):
    """CSE301 has classes on D1..D4. a: present 4/4; b: joined on D3, present D3 (1/2);
    c: present D1 only (1/4); left: removed on D2 (an active semester: not counted)."""

    def setUp(self):
        self.admin = client_for(make_admin('admin@example.com'))
        self.teacher = make_teacher('t@example.com', first_name='Nadia', last_name='Islam')
        self.a = make_student('a@example.com', 2302001)
        self.b = make_student('b@example.com', 2302002)
        self.c = make_student('c@example.com', 2302003)
        self.left = make_student('left@example.com', 2302004)
        self.semester = make_semester('Third', 'I', '2025-26', students=[self.a, self.b, self.c, self.left])
        StudentClassroom.objects.filter(student=self.b).update(joined_at=D3)
        StudentClassroom.objects.filter(student=self.left).update(left_at=D2)
        self.ci = make_course_info(self.teacher, 'CSE301', semester=self.semester)
        for day in (D1, D2, D3, D4):
            log(self.ci, self.a, day)
            log(self.ci, self.c, day, 'PRESENT' if day == D1 else 'ABSENT')
        log(self.ci, self.b, D3)
        log(self.ci, self.b, D4, 'ABSENT')
        log(self.ci, self.left, D1, 'ABSENT')
        # Two sessions on D4: present in either one counts once
        log(self.ci, self.a, D4, 'ABSENT')


class CourseNumbersTests(NumbersTestCase):
    def test_per_student_and_course(self):
        numbers = course_numbers([self.ci])[self.ci.id]
        self.assertEqual(numbers.classes_held, 4)
        self.assertEqual(numbers.class_dates, [D4, D3, D2, D1])
        self.assertEqual(numbers.student_count, 3)
        per = {p: (n.attended, n.held, n.percent, n.below_min) for p, n in numbers.students.items()}
        self.assertEqual(per, {
            self.a.id: (4, 4, 100.0, False),
            self.b.id: (1, 2, 50.0, True),
            self.c.id: (1, 4, 25.0, True),
        })
        self.assertEqual(numbers.average_percent, 58.3)  # (100 + 50 + 25) / 3
        self.assertEqual(numbers.below_min_count, 2)

    def test_finished_semester_counts_former_members(self):
        self.semester.is_active = False
        self.semester.save()
        numbers = course_numbers([self.ci])[self.ci.id]
        self.assertEqual(numbers.student_count, 4)
        left = numbers.students[self.left.id]
        self.assertEqual((left.attended, left.held, left.percent), (0, 1, 0.0))  # D1 only (left on D2)

    def test_no_classes(self):
        other = make_course_info(self.teacher, 'CSE303', semester=self.semester)
        numbers = course_numbers([other])[other.id]
        self.assertEqual((numbers.classes_held, numbers.average_percent, numbers.below_min_count), (0, None, 0))
        self.assertTrue(all(n.percent is None and not n.below_min for n in numbers.students.values()))


class CourseInfoListTests(NumbersTestCase):
    def test_rows(self):
        old = make_semester('Second', 'II', '2024-25', is_active=False)
        old_ci = make_course_info(self.teacher, 'CSE201', semester=old)
        gone = make_course_info(None, 'CSE303', semester=self.semester)
        CourseInfo.objects.filter(pk=gone.pk).update(deleted=True)

        res = self.admin.get('/api/admin/course-info/')

        self.assertEqual(res.status_code, 200)
        rows = res.data['course_infos']
        self.assertEqual([r['id'] for r in rows], [str(self.ci.id), str(old_ci.id)])  # active semester first
        self.assertEqual(rows[0], {
            'id': str(self.ci.id),
            'course': {'id': str(self.ci.course_id), 'code': 'CSE301', 'title': 'Course CSE301'},
            'teacher': {'id': str(self.teacher.id), 'name': 'Nadia Islam', 'deleted': False},
            'semester': {'id': str(self.semester.id), 'label': 'Level 3 · Term I · 2025-26', 'is_active': True},
            'student_count': 3,
            'classes_held': 4,
            'average_percent': 58.3,
        })
        self.assertEqual((rows[1]['classes_held'], rows[1]['average_percent']), (0, None))

        res = self.admin.get(f'/api/admin/course-info/?semester_id={old.id}')
        self.assertEqual([r['id'] for r in res.data['course_infos']], [str(old_ci.id)])
        res = self.admin.get('/api/admin/course-info/?semester_id=nope')
        self.assertEqual((res.status_code, res.data['message']), (400, 'This semester was not found.'))

    def test_admins_only(self):
        self.assertEqual(client_for(self.teacher.user).get('/api/admin/course-info/').status_code, 403)


class OverviewTests(NumbersTestCase):
    def test_overview(self):
        make_student('pending@example.com', 2302010, is_verified=False)
        make_student('deleted@example.com', 2302011, deleted=True, is_active=False)
        make_teacher('pending.t@example.com', is_verified=False)
        Course.objects.create(code='CSE999', title='Gone', deleted=True)
        make_semester('Fourth', 'I', '2025-26', is_active=False)  # finished: not listed

        # Saved sessions, newest saved first; a live one and a removed course's one are left out
        now = timezone.now()
        older = AttendanceSession.objects.create(course_info=self.ci, date=D1, mode='QR_ONLINE', is_active=False,
                                                 ended_at=now - datetime.timedelta(days=3))
        newer = AttendanceSession.objects.create(course_info=self.ci, date=D2, mode='FACE', is_active=False,
                                                 ended_at=now - datetime.timedelta(days=1))
        AttendanceSession.objects.create(course_info=self.ci, date=D4, mode='QR_ONLINE')
        removed = make_course_info(self.teacher, 'CSE305', semester=self.semester)
        AttendanceSession.objects.create(course_info=removed, date=D2, mode='FACE', is_active=False, ended_at=now)
        CourseInfo.objects.filter(pk=removed.pk).update(deleted=True)
        AttendanceLog.objects.filter(course_info=self.ci, date=D2).update(session=newer)

        res = self.admin.get('/api/admin/overview/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['counts'], {
            'students': 4, 'teachers': 1, 'courses': 2, 'active_semesters': 1, 'pending_approvals': 2,
        })
        self.assertEqual(res.data['semesters'], [{
            'id': str(self.semester.id), 'label': 'Level 3 · Term I · 2025-26',
            'student_count': 3, 'course_count': 1, 'average_percent': 58.3, 'below_min_count': 2,
        }])
        self.assertEqual(res.data['recent_sessions'], [
            {'session_id': str(newer.id), 'course_info_id': str(self.ci.id), 'course_code': 'CSE301',
             'course_title': 'Course CSE301', 'date': '2026-09-02', 'delivery': 'IN_CLASS', 'mode': 'FACE',
             'present': 1, 'total': 2},
            {'session_id': str(older.id), 'course_info_id': str(self.ci.id), 'course_code': 'CSE301',
             'course_title': 'Course CSE301', 'date': '2026-09-01', 'delivery': 'IN_CLASS', 'mode': 'QR_ONLINE',
             'present': 0, 'total': 0},
        ])

    def test_recent_sessions_leave_deleted_accounts_out(self):
        session = AttendanceSession.objects.create(course_info=self.ci, date=D3, mode='QR_ONLINE', is_active=False,
                                                   ended_at=timezone.now())
        AttendanceLog.objects.filter(course_info=self.ci, date=D3).update(session=session)  # a, b present; c absent
        before = self.admin.get('/api/admin/overview/').data['recent_sessions'][0]
        self.assertEqual((before['present'], before['total']), (2, 3))
        self.a.user.deleted = True
        self.a.user.save(update_fields=['deleted'])
        after = self.admin.get('/api/admin/overview/').data['recent_sessions'][0]
        self.assertEqual((after['present'], after['total']), (1, 2))  # as the course page counts it

    @override_settings(ATTENDANCE_MIN_PERCENT=50)
    def test_minimum_is_a_setting(self):
        res = self.admin.get('/api/admin/overview/')
        self.assertEqual(res.data['semesters'][0]['below_min_count'], 1)  # only c (25%); b has exactly 50%

    def test_only_ten_sessions(self):
        for i in range(12):
            AttendanceSession.objects.create(course_info=self.ci, date=D1, mode='FACE', is_active=False,
                                             ended_at=timezone.now())
        self.assertEqual(len(self.admin.get('/api/admin/overview/').data['recent_sessions']), 10)

    def test_admins_only(self):
        self.assertEqual(client_for(self.teacher.user).get('/api/admin/overview/').status_code, 403)
