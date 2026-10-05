import datetime

from django.test import TestCase

from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import client_for, make_course_info, make_student, make_teacher


class TeacherCourseDetailTests(TestCase):
    def test_attendance_counts_and_dates(self):
        teacher = make_teacher('teacher@example.com')
        a = make_student('a@example.com', 2302001)
        b = make_student('b@example.com', 2302002)
        ci = make_course_info(teacher, students=[a, b])
        day1, day2 = datetime.date(2026, 10, 1), datetime.date(2026, 10, 2)
        for student, day, status in [
            (a, day1, 'PRESENT'), (b, day1, 'ABSENT'),
            (a, day2, 'PRESENT'), (b, day2, 'PRESENT'),
        ]:
            AttendanceLog.objects.create(course_info=ci, student=student, date=day, status=status)

        res = client_for(teacher.user).get(f'/api/teacher/course-info/{ci.id}/')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['course']['classes_held'], 2)
        self.assertEqual({s['student_id']: (s['attended'], s['held']) for s in res.data['students']},
                         {2302001: (2, 2), 2302002: (1, 2)})
        self.assertEqual(res.data['dates'], [
            {'date': '2026-10-02', 'present': 2, 'total': 2},
            {'date': '2026-10-01', 'present': 1, 'total': 2},
        ])


class RollCallSaveTests(TestCase):
    def test_accepts_date_without_leading_zeros(self):
        teacher = make_teacher('teacher@example.com')
        a = make_student('a@example.com', 2302001)
        ci = make_course_info(teacher, students=[a])

        res = client_for(teacher.user).post(f'/api/teacher/course-info/{ci.id}/roll-call/', {
            'date': '2026-10-4', 'present_profile_ids': [str(a.id)],
        }, format='json')

        self.assertEqual(res.status_code, 200, res.data)
        log = AttendanceLog.objects.get(student=a)
        self.assertEqual((log.date, log.status), (datetime.date(2026, 10, 4), 'PRESENT'))

    def test_rejects_a_bad_date(self):
        teacher = make_teacher('teacher@example.com')
        ci = make_course_info(teacher)
        res = client_for(teacher.user).post(f'/api/teacher/course-info/{ci.id}/roll-call/', {
            'date': 'yesterday', 'present_profile_ids': [],
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_the_old_save_endpoint_is_gone(self):
        teacher = make_teacher('teacher@example.com')
        ci = make_course_info(teacher)
        res = client_for(teacher.user).post(f'/api/teacher/course-info/{ci.id}/history-session/', {
            'date': '2026-10-04', 'presentStudentIds': [],
        }, format='json')
        self.assertEqual(res.status_code, 404)


class SeveralSessionsOneDayTests(TestCase):
    """Two attendance sessions on one date count as one class."""

    def setUp(self):
        from apps.attendance.models import AttendanceSession
        self.teacher = make_teacher('teacher@example.com')
        self.a = make_student('a@example.com', 2302001)
        self.b = make_student('b@example.com', 2302002)
        self.ci = make_course_info(self.teacher, students=[self.a, self.b])
        day = datetime.date(2026, 10, 5)
        for mode, statuses in [('QR_ONLINE', ('PRESENT', 'ABSENT')), ('FACE', ('PRESENT', 'PRESENT'))]:
            session = AttendanceSession.objects.create(
                course_info=self.ci, date=day, mode=mode, is_active=False,
            )
            for student, status in zip((self.a, self.b), statuses):
                AttendanceLog.objects.create(
                    session=session, course_info=self.ci, student=student, date=day, status=status,
                )

    def test_teacher_course_detail(self):
        res = client_for(self.teacher.user).get(f'/api/teacher/course-info/{self.ci.id}/')
        self.assertEqual(res.data['course']['classes_held'], 1)
        self.assertEqual([(s['attended'], s['held'], s['percent']) for s in res.data['students']],
                         [(1, 1, 100.0), (1, 1, 100.0)])
        self.assertEqual(res.data['dates'], [{'date': '2026-10-05', 'present': 2, 'total': 2}])

    def test_student_summary(self):
        res = client_for(self.b.user).get(f'/api/student/{self.b.id}/semesters/')
        course = res.data['semesters'][0]['courses'][0]
        self.assertEqual((course['totalClasses'], course['presentCount'], course['percentage']), (1, 1, 100.0))
        self.assertEqual((course['held'], course['attended'], course['percent']), (1, 1, 100.0))
        self.assertEqual(course['history'], [{'date': '2026-10-05', 'present': True}])

    def test_history_has_one_row_per_student_and_both_sessions(self):
        res = client_for(self.teacher.user).get(f'/api/sessions/course-info/{self.ci.id}/history/')
        [day] = res.data['history']
        self.assertEqual([s['mode'] for s in day['sessions']], ['QR_ONLINE', 'FACE'])
        self.assertEqual([(log['student_id'], log['status']) for log in day['logs']],
                         [(2302001, 'PRESENT'), (2302002, 'PRESENT')])

    def test_export(self):
        res = client_for(self.teacher.user).get(f'/api/reports/course-info/{self.ci.id}/export/?export_format=csv')
        rows = res.content.decode().splitlines()
        self.assertIn('2302002', rows[2])
        self.assertTrue(rows[2].endswith('PRESENT,1,1,100.0%'), rows[2])
