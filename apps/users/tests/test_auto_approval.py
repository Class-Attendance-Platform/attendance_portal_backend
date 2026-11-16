from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.attendance.tests.helpers import client_for, make_admin, make_teacher
from apps.faces.tests.test_faces import FakeEngine, face, person
from apps.users.models import ApprovalSetting, User


class AutoApprovalTests(TestCase):
    def setUp(self):
        self.admin = make_admin('admin@example.com')
        self.client = client_for(self.admin)
        ApprovalSetting.objects.all().delete()

    def test_anonymous_cannot_access(self):
        res = APIClient().get('/api/admin/auto-approval/')
        self.assertEqual(res.status_code, 401)

    def test_teacher_cannot_access(self):
        teacher = make_teacher('teacher@example.com')
        res = client_for(teacher.user).get('/api/admin/auto-approval/')
        self.assertEqual(res.status_code, 403)

    def test_get_default_settings(self):
        res = self.client.get('/api/admin/auto-approval/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {
            'success': True,
            'settings': {
                'admin': False,
                'teacher': False,
                'student': False,
            },
        })

    def test_patch_settings(self):
        res = self.client.patch('/api/admin/auto-approval/', {'student': True, 'teacher': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['settings'], {
            'admin': False,
            'teacher': True,
            'student': True,
        })
        self.assertTrue(res.data['success'])

        # Toggle individual role
        res2 = self.client.patch('/api/admin/auto-approval/', {'admin': True, 'student': False}, format='json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['settings'], {
            'admin': True,
            'teacher': True,
            'student': False,
        })

    def test_registration_immediately_marks_student_and_teacher_verified(self):
        ApprovalSetting.objects.update_or_create(
            id=ApprovalSetting.get_settings().id,
            defaults={'auto_approve_student': False, 'auto_approve_teacher': False},
        )
        engine = FakeEngine()
        anon = APIClient()
        with mock.patch('apps.faces.services.get_engine', return_value=engine):
            res = anon.post('/api/auth/register/', {
                'role': 'STUDENT',
                'email': 'student.signup@example.com',
                'password': 'StrongPassword123!',
                'first_name': 'Direct',
                'last_name': 'Student',
                'student_id': 2402099,
                'current_level': 'Third',
                'current_semester': 'I',
                'consent': 'true',
                'straight': engine.photo((1, 0, 0), [face(person(1, 0.1, 1))]),
                'left': engine.photo((2, 0, 0), [face(person(1, 0.1, 2))]),
                'right': engine.photo((3, 0, 0), [face(person(1, 0.1, 3))]),
            }, format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['status'], 'approved')
        self.assertIn('access', res.data)
        self.assertTrue(User.objects.get(email='student.signup@example.com').is_verified)

        teacher_res = anon.post('/api/auth/register/', {
            'role': 'TEACHER',
            'email': 'teacher.signup@example.com',
            'password': 'StrongPassword123!',
            'first_name': 'Direct',
            'last_name': 'Teacher',
            'employee_id': 'EMP-2099',
        }, format='json')
        self.assertEqual(teacher_res.status_code, 201, teacher_res.data)
        self.assertEqual(teacher_res.data['status'], 'approved')
        self.assertIn('access', teacher_res.data)
        self.assertTrue(User.objects.get(email='teacher.signup@example.com').is_verified)
