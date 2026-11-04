from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.attendance.tests.helpers import client_for, make_admin, make_student, make_teacher
from apps.users.constants import DEPARTMENT, FACULTY
from apps.users.models import StudentProfile, TeacherProfile, User

STRONG = 'Strong-pass-2026'


class PublicRegisterTests(TestCase):
    def setUp(self):
        cache.clear()  # sign-up rate limit counts live in the cache
        self.client = APIClient()

    def payload(self, role, **extra):
        email = f'{role.lower()}@example.com'
        return {
            'email': email,
            'password': STRONG,
            'first_name': 'Test',
            'last_name': 'User',
            'role': role,
            **extra,
        }

    def student(self, **extra):
        return self.payload('STUDENT', **{'student_id': 2302001, 'current_level': 'Third', 'current_semester': 'I', **extra})

    def register(self, data):
        return self.client.post('/api/auth/register/', data, format='json')

    def test_email_longer_than_150_characters(self):
        email = f'{"a" * 140}@example.com'  # 152: valid, but the username (= email) holds 150
        res = self.register(self.student(email=email))
        self.assertEqual(res.status_code, 400)
        self.assertIn('email', res.data['errors'])
        self.assertFalse(User.objects.exists())

    def test_cannot_register_as_admin(self):
        res = self.register(self.payload('ADMIN'))
        self.assertEqual(res.status_code, 400)
        self.assertIn('role', res.data['errors'])
        self.assertEqual(res.data['message'], 'Admin accounts cannot be created by sign-up.')
        self.assertFalse(User.objects.filter(role='ADMIN').exists())

    def test_student_sign_up_waits_for_approval_and_gets_no_tokens(self):
        res = self.register(self.student(email='New.Student@Example.COM'))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data, {
            'success': True, 'status': 'pending', 'message': 'Account created. An admin will approve it soon.',
        })
        user = User.objects.get()
        self.assertEqual(user.email, 'new.student@example.com')  # stored lower case
        self.assertEqual(user.username, 'new.student@example.com')
        self.assertFalse(user.is_verified)
        self.assertEqual((user.faculty, user.department), (FACULTY, DEPARTMENT))
        self.assertEqual(user.student_profile.student_id, 2302001)
        self.assertEqual(user.student_profile.current_level, 'Third')

    def test_teacher_sign_up(self):
        res = self.register(self.payload('TEACHER', employee_id=' EMP-1 '))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(TeacherProfile.objects.get().employee_id, 'EMP-1')
        self.assertNotIn('tokens', res.data)

    def test_faculty_and_department_are_set_by_the_server(self):
        self.register(self.payload('TEACHER', employee_id='EMP-1', faculty='AGRICULTURE', department='X'))
        user = User.objects.get()
        self.assertEqual((user.faculty, user.department), (FACULTY, DEPARTMENT))

    def test_pending_account_cannot_sign_in_until_approved(self):
        self.register(self.student())
        login = {'email': 'student@example.com', 'password': STRONG}
        res = self.client.post('/api/auth/login/', login, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'pending_approval')
        self.assertNotIn('access', res.data)

        user = User.objects.get(email='student@example.com')
        client_for(make_admin('admin@example.com')).post(f'/api/admin/users/{user.id}/verify/')
        res = self.client.post('/api/auth/login/', login, format='json')
        self.assertEqual(res.status_code, 200)

    def test_duplicate_email_ignores_capitals(self):
        make_student('Student@Example.com', 2302999)
        res = self.register(self.student(email='STUDENT@example.com'))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['errors']['email'], ['An account with this email already exists.'])
        self.assertEqual(res.data['message'], 'An account with this email already exists.')
        self.assertEqual(User.objects.count(), 1)

    def test_duplicate_student_id(self):
        make_student('other@example.com', 2302001)
        res = self.register(self.student())
        self.assertEqual(res.status_code, 400)
        self.assertIn('student_id', res.data['errors'])
        self.assertFalse(User.objects.filter(email='student@example.com').exists())

    def test_duplicate_employee_id_ignores_capitals(self):
        make_teacher('emp-1')  # factory uses the email as employee id
        res = self.register(self.payload('TEACHER', employee_id='EMP-1'))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['errors']['employee_id'], ['A teacher with this employee ID already exists.'])

    def test_password_rules(self):
        for password, words in (('83920571946', 'numeric'), ('Sh0rt-1', 'too short'), ('password123', 'too common')):
            res = self.register(self.student(password=password))
            self.assertEqual(res.status_code, 400, password)
            self.assertIn('password', res.data['errors'])
            self.assertIn(words, res.data['message'])
        self.assertFalse(User.objects.exists())

    def test_missing_student_fields(self):
        data = self.payload('STUDENT', current_level='Third', current_semester='I')
        res = self.register(data)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Student ID is required for students.')

    def test_unknown_level_reads_plainly(self):
        res = self.register(self.student(current_level='Fifth'))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Level must be First, Second, Third or Fourth.')

    def test_a_failed_profile_save_leaves_no_half_made_account(self):
        with mock.patch.object(StudentProfile.objects, 'create', side_effect=IntegrityError('unique')):
            res = self.register(self.student())
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.data['message'])
        self.assertFalse(User.objects.exists())
