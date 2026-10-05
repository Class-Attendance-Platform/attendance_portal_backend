from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User


class PublicRegisterTests(TestCase):
    def payload(self, role, **extra):
        email = f'{role.lower()}@example.com'
        return {
            'username': email,  # the sign-up page sends the email as username
            'email': email,
            'password': 'Strong-pass-2026',
            'first_name': 'Test',
            'last_name': 'User',
            'role': role,
            **extra,
        }

    def test_cannot_register_as_admin(self):
        res = APIClient().post('/api/auth/register/', self.payload('ADMIN'), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(role='ADMIN').exists())

    def test_can_register_as_student(self):
        res = APIClient().post('/api/auth/register/', self.payload(
            'STUDENT', student_id=2302001, current_level='Third', current_semester='I',
        ), format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['user']['student_profile']['student_id'], 2302001)

    def test_can_register_as_teacher(self):
        res = APIClient().post('/api/auth/register/', self.payload('TEACHER', employee_id='EMP-1'), format='json')
        self.assertEqual(res.status_code, 201)
