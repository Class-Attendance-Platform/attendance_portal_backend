"""Who may read a student's data: the device lookup and the semesters summary."""
from django.test import TestCase

from apps.attendance.tests.helpers import client_for, make_admin, make_student, make_teacher


class DeviceLookupTests(TestCase):
    """/student/verify-device/<student_id>/ is for the offline laptop server (teacher's token)."""

    def setUp(self):
        self.alice = make_student('alice@example.com', 2302001)
        self.bob = make_student('bob@example.com', 2302002, first_name='Bob', last_name='Secret')

    def lookup(self, user, student_id):
        return client_for(user).get(f'/api/student/verify-device/{student_id}/?mac_address=x')

    def test_students_cannot_look_up_anyone(self):
        for student_id in (2302002, 2302001, 2302999):  # another, their own, unknown: all the same answer
            res = self.lookup(self.alice.user, student_id)
            self.assertEqual((res.status_code, res.data['code']), (403, 'permission_denied'))
            self.assertNotIn('name', res.data)
            self.assertNotIn('profile_uuid', res.data)

    def test_teachers_and_admins_may(self):
        for user in (make_teacher('t@example.com').user, make_admin('admin@example.com')):
            res = self.lookup(user, 2302002)
            self.assertEqual(res.status_code, 200)
            self.assertEqual((res.data['name'], res.data['profile_uuid']), ('Bob Secret', str(self.bob.id)))


class StudentSummaryAccessTests(TestCase):
    def setUp(self):
        self.student = make_student('s@example.com', 2302001)
        self.url = f'/api/student/{self.student.id}/semesters/'

    def test_teachers_use_their_course_pages_instead(self):
        res = client_for(make_teacher('t@example.com').user).get(self.url)
        self.assertEqual((res.status_code, res.data['code']), (403, 'permission_denied'))
        self.assertNotIn('semesters', res.data)

    def test_the_student_and_admins_may(self):
        self.assertEqual(client_for(self.student.user).get(self.url).status_code, 200)
        self.assertEqual(client_for(make_admin('admin@example.com')).get(self.url).status_code, 200)
