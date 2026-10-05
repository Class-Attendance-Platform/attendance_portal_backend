from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.attendance.tests.helpers import PASSWORD, client_for, make_admin, make_student, make_teacher
from apps.users.models import User
from apps.users.services import password_reset_link

NEW_PASSWORD = 'Brand-new-pass-77'


class AuthTestCase(TestCase):
    def setUp(self):
        cache.clear()  # rate limit counts live in the cache
        self.client = APIClient()
        self.student = make_student('student@example.com', 2302001, first_name='Ayesha', last_name='Rahman')

    def login(self, email='student@example.com', password=PASSWORD):
        return self.client.post('/api/auth/login/', {'email': email, 'password': password}, format='json')

    def refresh(self, token):
        return self.client.post('/api/auth/refresh/', {'refresh': token}, format='json')


class LoginTests(AuthTestCase):
    def test_success_returns_tokens_and_the_user(self):
        res = self.login()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['success'])
        self.assertTrue(res.data['access'] and res.data['refresh'])
        user = res.data['user']
        self.assertEqual(set(user), {
            'id', 'userName', 'first_name', 'last_name', 'email', 'role', 'is_verified', 'date_joined',
            'student_profile', 'teacher_profile',
        })
        self.assertEqual(user['userName'], 'Ayesha Rahman')
        self.assertEqual(user['role'], 'STUDENT')
        self.assertEqual(user['student_profile'], {
            'id': str(self.student.id), 'student_id': 2302001, 'current_level': 'Third', 'current_semester': 'I',
        })
        self.assertIsNone(user['teacher_profile'])
        self.assertTrue(user['date_joined'].endswith('+06:00'))  # Asia/Dhaka
        self.student.user.refresh_from_db()
        self.assertIsNotNone(self.student.user.last_login)

    def test_teacher_profile(self):
        teacher = make_teacher('teacher@example.com')
        res = self.login('teacher@example.com')
        self.assertEqual(res.data['user']['teacher_profile'], {'id': str(teacher.id), 'employee_id': 'teacher@example.com'})
        self.assertIsNone(res.data['user']['student_profile'])

    def test_email_ignores_capitals_and_spaces(self):
        self.assertEqual(self.login('  STUDENT@Example.com ').status_code, 200)

    def test_wrong_password_or_unknown_email(self):
        for email, password in (('student@example.com', 'wrong-pass'), ('nobody@example.com', PASSWORD)):
            res = self.login(email, password)
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.data['code'], 'invalid_credentials')
            self.assertEqual(res.data['message'], 'Email or password is incorrect.')
            self.assertFalse(res.data['success'])

    def test_pending_account(self):
        User.objects.filter(pk=self.student.user.pk).update(is_verified=False)
        res = self.login()
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data['code'], 'pending_approval')
        self.assertEqual(res.data['message'], 'Your account is waiting for admin approval.')
        self.assertNotIn('access', res.data)
        # A wrong password tells nothing about the account
        self.assertEqual(self.login(password='wrong-pass').data['code'], 'invalid_credentials')

    def test_disabled_account(self):
        for change in ({'deleted': True, 'is_active': False}, {'deleted': False, 'is_active': False}):
            User.objects.filter(pk=self.student.user.pk).update(**change)
            res = self.login()
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.data['code'], 'account_disabled')
            self.assertEqual(res.data['message'], 'This account has been disabled. Contact the department office.')

    def test_admins_are_never_pending(self):
        admin = make_admin('admin@example.com')
        User.objects.filter(pk=admin.pk).update(is_verified=False)  # e.g. made by createsuperuser
        self.assertEqual(self.login('admin@example.com').status_code, 200)

    def test_a_stale_token_in_the_request_is_ignored(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not-a-token')
        self.assertEqual(self.login().status_code, 200)

    def test_missing_password(self):
        res = self.client.post('/api/auth/login/', {'email': 'student@example.com'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Password is required.')

    def test_tokens_still_rotate_on_refresh(self):
        old = self.login().data['refresh']
        res = self.refresh(old)
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.data['refresh'], old)
        self.assertEqual(self.refresh(old).status_code, 401)  # the old one is blacklisted


class LogoutTests(AuthTestCase):
    def test_logout_blacklists_the_refresh_token(self):
        tokens = self.login().data
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {tokens["access"]}')
        res = self.client.post('/api/auth/logout/', {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['success'])
        self.client.credentials()
        self.assertEqual(self.refresh(tokens['refresh']).status_code, 401)

    def test_an_invalid_token_is_fine(self):
        client = client_for(self.student.user)
        for body in ({'refresh': 'garbage'}, {}):
            self.assertEqual(client.post('/api/auth/logout/', body, format='json').status_code, 200)

    def test_cannot_sign_out_someone_else(self):
        make_student('other@example.com', 2302002)
        others = self.login('other@example.com').data['refresh']
        client_for(self.student.user).post('/api/auth/logout/', {'refresh': others}, format='json')
        self.assertEqual(self.refresh(others).status_code, 200)

    def test_needs_sign_in(self):
        res = self.client.post('/api/auth/logout/', {'refresh': 'x'}, format='json')
        self.assertEqual(res.status_code, 401)


class MeTests(AuthTestCase):
    def test_get(self):
        res = client_for(self.student.user).get('/api/auth/me/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user']['email'], 'student@example.com')
        self.assertEqual(res.data['user']['student_profile']['student_id'], 2302001)

    def test_patch_changes_only_the_name(self):
        res = client_for(self.student.user).patch('/api/auth/me/', {
            'first_name': ' Nusrat ', 'last_name': 'Jahan', 'email': 'new@example.com', 'role': 'ADMIN',
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user']['userName'], 'Nusrat Jahan')
        user = User.objects.get(pk=self.student.user.pk)
        self.assertEqual((user.first_name, user.last_name), ('Nusrat', 'Jahan'))
        self.assertEqual((user.email, user.role), ('student@example.com', 'STUDENT'))

    def test_patch_one_name(self):
        res = client_for(self.student.user).patch('/api/auth/me/', {'last_name': 'Begum'}, format='json')
        self.assertEqual(res.data['user']['userName'], 'Ayesha Begum')

    def test_first_name_cannot_be_blank(self):
        res = client_for(self.student.user).patch('/api/auth/me/', {'first_name': ''}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'First name is required.')


class PasswordChangeTests(AuthTestCase):
    def change(self, client, current, new):
        return client.post('/api/auth/password/change/',
                           {'current_password': current, 'new_password': new}, format='json')

    def test_wrong_current_password(self):
        res = self.change(client_for(self.student.user), 'wrong-pass', NEW_PASSWORD)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'wrong_password')
        self.assertTrue(res.data['message'])

    def test_weak_new_password(self):
        res = self.change(client_for(self.student.user), PASSWORD, '12345678')
        self.assertEqual(res.status_code, 400)
        self.assertIn('new_password', res.data['errors'])
        self.assertNotIn('new_password:', res.data['message'])

    def test_same_password_again(self):
        res = self.change(client_for(self.student.user), PASSWORD, PASSWORD)
        self.assertEqual(res.status_code, 400)
        self.assertIn('different', res.data['message'])

    def test_success_signs_out_other_devices_and_returns_fresh_tokens(self):
        other_device = self.login().data['refresh']
        res = self.change(client_for(self.student.user), PASSWORD, NEW_PASSWORD)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.refresh(other_device).status_code, 401)
        self.assertEqual(self.refresh(res.data['tokens']['refresh']).status_code, 200)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code, 200)
        self.assertEqual(self.login(password=PASSWORD).status_code, 401)


class PasswordForgotTests(AuthTestCase):
    def forgot(self, email):
        return self.client.post('/api/auth/password/forgot/', {'email': email}, format='json')

    def test_sends_a_reset_link(self):
        res = self.forgot('Student@Example.com')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['message'],
                         'If an account exists for this email, we sent a link to reset the password.')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['student@example.com'])
        self.assertIn('http://localhost:8081/reset-password?uid=', message.body)
        self.assertIn('&token=', message.body)

    def test_unknown_email_gets_the_same_answer(self):
        res = self.forgot('nobody@example.com')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_pending_or_disabled_accounts_get_no_email(self):
        for change in ({'is_verified': False}, {'is_verified': True, 'deleted': True, 'is_active': False}):
            User.objects.filter(pk=self.student.user.pk).update(**change)
            self.assertEqual(self.forgot('student@example.com').status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_HOST_USER='')
    def test_email_not_configured(self):
        res = self.forgot('student@example.com')
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data['code'], 'email_not_configured')
        self.assertEqual(res.data['message'],
                         'Password reset by email is not set up. Ask an admin to reset your password.')
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email(self):
        res = self.forgot('not-an-email')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Enter a valid email address.')


class PasswordResetTests(AuthTestCase):
    def link_parts(self):
        user = User.objects.get(pk=self.student.user.pk)  # the link depends on the saved last login
        query = parse_qs(urlparse(password_reset_link(user)).query)
        return query['uid'][0], query['token'][0]

    def reset(self, uid, token, password=NEW_PASSWORD):
        return self.client.post('/api/auth/password/reset/',
                                {'uid': uid, 'token': token, 'new_password': password}, format='json')

    def test_reset_works_once_and_signs_out_everywhere(self):
        signed_in = self.login().data['refresh']
        uid, token = self.link_parts()
        res = self.reset(uid, token)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['message'])
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code, 200)
        self.assertEqual(self.refresh(signed_in).status_code, 401)

        again = self.reset(uid, token, 'Another-pass-88')
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data['code'], 'invalid_link')

    def test_invalid_links(self):
        uid, token = self.link_parts()
        for bad_uid, bad_token in ((uid, 'nope-123'), ('bm90LWEtdXVpZA', token), ('***', token)):
            res = self.reset(bad_uid, bad_token)
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.data['code'], 'invalid_link')
            self.assertEqual(res.data['message'], 'This link is invalid or has expired.')

    def test_weak_password_keeps_the_link_usable(self):
        uid, token = self.link_parts()
        res = self.reset(uid, token, '12345678')
        self.assertEqual(res.status_code, 400)
        self.assertIn('new_password', res.data['errors'])
        self.assertEqual(self.reset(uid, token).status_code, 200)

    def test_disabled_account_link_stops_working(self):
        uid, token = self.link_parts()
        User.objects.filter(pk=self.student.user.pk).update(deleted=True, is_active=False)
        self.assertEqual(self.reset(uid, token).data['code'], 'invalid_link')


class OldAccessTokensStopAfterAPasswordChangeTests(AuthTestCase):
    """Access tokens carry a fingerprint of the password (CHECK_REVOKE_TOKEN, apps/users/jwt.py)."""

    def me(self, access):
        return APIClient().get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')

    def assert_refused(self, res):
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data['code'], 'password_changed')
        self.assertEqual(res.data['message'], 'Your password was changed. Please sign in again.')

    def test_password_change_ends_the_other_devices_access_at_once(self):
        other = self.login().data
        self.assertEqual(self.me(other['access']).status_code, 200)
        res = client_for(self.student.user).post(
            '/api/auth/password/change/', {'current_password': PASSWORD, 'new_password': NEW_PASSWORD}, format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assert_refused(self.me(other['access']))
        self.assertEqual(self.refresh(other['refresh']).status_code, 401)
        # The device that changed it goes on with the fresh pair
        self.assertEqual(self.me(res.data['tokens']['access']).status_code, 200)
        self.assertEqual(self.refresh(res.data['tokens']['refresh']).status_code, 200)

    def test_reset_by_email_ends_old_access_tokens(self):
        other = self.login().data
        user = User.objects.get(pk=self.student.user.pk)
        query = parse_qs(urlparse(password_reset_link(user)).query)
        res = self.client.post('/api/auth/password/reset/', {
            'uid': query['uid'][0], 'token': query['token'][0], 'new_password': NEW_PASSWORD,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assert_refused(self.me(other['access']))
        self.assertEqual(self.refresh(other['refresh']).status_code, 401)

    def test_admin_reset_ends_old_access_tokens(self):
        other = self.login().data
        admin = client_for(make_admin('admin@example.com'))
        res = admin.post(f'/api/admin/users/{self.student.user.id}/reset-password/',
                         {'new_password': NEW_PASSWORD}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assert_refused(self.me(other['access']))
        self.assertEqual(self.refresh(other['refresh']).status_code, 401)
        self.assertEqual(self.me(self.login(password=NEW_PASSWORD).data['access']).status_code, 200)

    def test_tokens_from_before_the_switch_keep_working_after_one_refresh(self):
        # A pair made without the fingerprint (as before CHECK_REVOKE_TOKEN was on)
        refresh = RefreshToken.for_user(self.student.user)
        del refresh['hash_password']
        old_access = refresh.access_token
        self.assertNotIn('hash_password', old_access.payload)
        self.assertEqual(self.me(str(old_access)).status_code, 401)  # the app refreshes on a 401
        res = self.refresh(str(refresh))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.me(res.data['access']).status_code, 200)
        self.assertEqual(self.refresh(res.data['refresh']).status_code, 200)

    def test_a_token_from_before_a_password_change_cannot_be_refreshed(self):
        # Even if it was not blacklisted (e.g. a password set outside the app)
        refresh = RefreshToken.for_user(self.student.user)
        user = User.objects.get(pk=self.student.user.pk)
        user.set_password(NEW_PASSWORD)
        user.save()
        self.assert_refused(self.refresh(str(refresh)))
        self.assert_refused(self.me(str(refresh.access_token)))


class AppConfigTests(TestCase):
    def test_app_config(self):
        res = APIClient().get('/api/config/app/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {
            'success': True,
            'app_name': 'HSTU Attendance Portal',
            'faculty': 'Computer Science and Engineering',
            'department': 'CSE',
            'attendance_min_percent': 75,
            'email_reset_enabled': True,
            'min_app_version': '1.0.0',
            'latest_app_version': '1.0.0',
            'download_url': 'https://github.com/Class-Attendance-Platform/attendance_portal_frontend/releases/latest',
            'levels': ['First', 'Second', 'Third', 'Fourth'],
            'terms': ['I', 'II'],
        })

    @override_settings(EMAIL_HOST_USER='', MIN_APP_VERSION='1.2.0', LATEST_APP_VERSION='1.3.0')
    def test_settings_feed_it(self):
        res = APIClient().get('/api/config/app/')
        self.assertFalse(res.data['email_reset_enabled'])
        self.assertEqual((res.data['min_app_version'], res.data['latest_app_version']), ('1.2.0', '1.3.0'))

    def test_old_config_endpoints_stay(self):
        for name in ('faculties', 'departments', 'credits'):
            self.assertEqual(APIClient().get(f'/api/config/{name}/').status_code, 200)
