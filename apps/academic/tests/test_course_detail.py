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
