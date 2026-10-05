import datetime

from django.test import TestCase

from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import client_for, make_course_info, make_student, make_teacher


class TeacherCourseDetailTests(TestCase):
    def test_attendance_counts_and_history(self):
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
        attendance = res.data['courseInfo']['attendance']
        self.assertEqual(attendance['totalClasses'], 2)
        self.assertEqual(attendance['attendanceMap'], {2302001: 2, 2302002: 1})
        self.assertEqual(attendance['history'], [
            {'date': '2026-10-02', 'presentStudents': [2302001, 2302002]},
            {'date': '2026-10-01', 'presentStudents': [2302001]},
        ])


class RollCallSaveTests(TestCase):
    def test_accepts_date_without_leading_zeros(self):
        teacher = make_teacher('teacher@example.com')
        a = make_student('a@example.com', 2302001)
        ci = make_course_info(teacher, students=[a])

        res = client_for(teacher.user).post(f'/api/teacher/course-info/{ci.id}/history-session/', {
            'date': '2026-10-4', 'presentStudentIds': ['2302001'],
        }, format='json')

        self.assertEqual(res.status_code, 200)
        log = AttendanceLog.objects.get(student=a)
        self.assertEqual((log.date, log.status), (datetime.date(2026, 10, 4), 'PRESENT'))

    def test_rejects_a_bad_date(self):
        teacher = make_teacher('teacher@example.com')
        ci = make_course_info(teacher)
        res = client_for(teacher.user).post(f'/api/teacher/course-info/{ci.id}/history-session/', {
            'date': 'yesterday', 'presentStudentIds': [],
        }, format='json')
        self.assertEqual(res.status_code, 400)


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
        attendance = client_for(self.teacher.user).get(f'/api/teacher/course-info/{self.ci.id}/').data['courseInfo']['attendance']
        self.assertEqual(attendance['totalClasses'], 1)
        self.assertEqual(attendance['attendanceMap'], {2302001: 1, 2302002: 1})
        self.assertEqual(attendance['history'], [{'date': '2026-10-05', 'presentStudents': [2302001, 2302002]}])

    def test_student_summary(self):
        res = client_for(self.b.user).get(f'/api/student/{self.b.id}/semesters/')
        course = res.data['semesters'][0]['courses'][0]
        self.assertEqual((course['totalClasses'], course['presentCount'], course['percentage']), (1, 1, 100.0))
        self.assertEqual(course['history'], [{'date': '2026-10-05', 'present': True}])

    def test_export(self):
        res = client_for(self.teacher.user).get(f'/api/reports/course-info/{self.ci.id}/export/?export_format=csv')
        rows = res.content.decode().splitlines()
        self.assertIn('2302002', rows[2])
        self.assertTrue(rows[2].endswith('PRESENT,1,1,100.0%'), rows[2])
