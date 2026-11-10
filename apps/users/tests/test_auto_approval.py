from django.test import TestCase
from rest_framework.test import APIClient

from apps.attendance.tests.helpers import client_for, make_admin, make_teacher
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

    def test_registration_with_auto_approval_enabled(self):
        # Enable student auto-approval
        ApprovalSetting.objects.update_or_create(id=ApprovalSetting.get_settings().id, defaults={'auto_approve_student': True})
        anon = APIClient()
        res = anon.post('/api/auth/register/', {
            'role': 'STUDENT',
            'email': 'auto.approved@example.com',
            'password': 'StrongPassword123!',
            'first_name': 'Auto',
            'last_name': 'Approved',
            'student_id': 2402099,
            'current_level': 'Third',
            'current_semester': 'I',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'approved')

        user = User.objects.get(email='auto.approved@example.com')
        self.assertTrue(user.is_verified)

        # They can sign in immediately
        login_res = anon.post('/api/auth/login/', {'email': 'auto.approved@example.com', 'password': 'StrongPassword123!'}, format='json')
        self.assertEqual(login_res.status_code, 200)
        self.assertIn('access', login_res.data)

    def test_registration_with_auto_approval_disabled(self):
        ApprovalSetting.objects.update_or_create(id=ApprovalSetting.get_settings().id, defaults={'auto_approve_student': False})
        anon = APIClient()
        res = anon.post('/api/auth/register/', {
            'role': 'STUDENT',
            'email': 'manual.pending@example.com',
            'password': 'StrongPassword123!',
            'first_name': 'Manual',
            'last_name': 'Pending',
            'student_id': 2402098,
            'current_level': 'Third',
            'current_semester': 'I',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'pending')

        user = User.objects.get(email='manual.pending@example.com')
        self.assertFalse(user.is_verified)

        # Cannot sign in yet
        login_res = anon.post('/api/auth/login/', {'email': 'manual.pending@example.com', 'password': 'StrongPassword123!'}, format='json')
        self.assertEqual(login_res.status_code, 403)
        self.assertEqual(login_res.data['code'], 'pending_approval')
