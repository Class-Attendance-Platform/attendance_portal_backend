"""Request limits: login 10/min per IP + email (300/min per IP), register 10/hour per IP + email
(300/hour per IP), password forgot 20/hour per IP, check-in 30/min per user."""
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from apps.attendance.tests.helpers import client_for, make_student
from apps.users.throttles import LoginIPThrottle, RegisterIPThrottle


class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def assert_throttled(self, res):
        self.assertEqual(res.status_code, 429)
        self.assertFalse(res.data['success'])
        self.assertEqual(res.data['code'], 'throttled')
        self.assertTrue(res.data['message'].startswith('Too many attempts. Please try again in'))
        self.assertIn('Retry-After', res.headers)

    def test_login_10_per_minute_per_ip_and_email(self):
        client = APIClient()
        body = {'email': 'nobody@example.com', 'password': 'wrong-pass'}
        for _ in range(10):
            self.assertEqual(client.post('/api/auth/login/', body, format='json').status_code, 401)
        self.assert_throttled(client.post('/api/auth/login/', body, format='json'))
        # Capitals don't make a new count
        self.assert_throttled(client.post('/api/auth/login/', {**body, 'email': 'NoBody@Example.com'}, format='json'))
        # Another address is counted on its own
        other = client.post('/api/auth/login/', body, format='json', REMOTE_ADDR='10.0.0.9')
        self.assertEqual(other.status_code, 401)

    def test_a_whole_class_can_sign_in_from_one_ip(self):
        client = APIClient()
        for n in range(60):  # 60 students, one campus IP, each signing in once
            body = {'email': f'student{n}@example.com', 'password': 'wrong-pass'}
            self.assertEqual(client.post('/api/auth/login/', body, format='json').status_code, 401)

    def test_login_has_a_wide_cap_per_ip(self):
        client = APIClient()
        with mock.patch.dict(LoginIPThrottle.THROTTLE_RATES, {'login_ip': '20/min'}):
            for n in range(20):
                body = {'email': f'spray{n}@example.com', 'password': 'wrong-pass'}
                self.assertEqual(client.post('/api/auth/login/', body, format='json').status_code, 401)
            self.assert_throttled(client.post('/api/auth/login/', {'email': 'x@example.com', 'password': 'p'}, format='json'))

    def test_register_10_per_hour_per_ip_and_email(self):
        client = APIClient()
        body = {'role': 'STUDENT', 'email': 'retry@example.com', 'password': 'password123'}
        for _ in range(10):  # e.g. "This password is too common" again and again
            self.assertEqual(client.post('/api/auth/register/', body, format='json').status_code, 400)
        self.assert_throttled(client.post('/api/auth/register/', body, format='json'))
        self.assert_throttled(client.post('/api/auth/register/', {**body, 'email': 'Retry@Example.com'}, format='json'))
        # Another student on the same IP is counted on their own
        other = client.post('/api/auth/register/', {**body, 'email': 'next@example.com'}, format='json')
        self.assertEqual(other.status_code, 400)

    def test_a_class_of_100_signing_up_from_one_ip_with_retries_is_not_blocked(self):
        client = APIClient()
        for n in range(100):
            body = {
                'role': 'STUDENT', 'email': f'student{n}@example.com', 'first_name': 'Class', 'last_name': f'Member {n}',
                'student_id': 2302100 + n, 'current_level': 'Third', 'current_semester': 'I',
            }
            if n % 10 == 0:  # some try a common password first
                res = client.post('/api/auth/register/', {**body, 'password': 'password123'}, format='json')
                self.assertEqual(res.status_code, 400)
            res = client.post('/api/auth/register/', {**body, 'password': 'Tiger-lake-2026'}, format='json')
            self.assertEqual(res.status_code, 201, res.data)

    def test_register_has_a_wide_cap_per_ip(self):
        client = APIClient()
        with mock.patch.dict(RegisterIPThrottle.THROTTLE_RATES, {'register_ip': '20/hour'}):
            for n in range(20):
                body = {'email': f'probe{n}@example.com'}
                self.assertEqual(client.post('/api/auth/register/', body, format='json').status_code, 400)
            self.assert_throttled(client.post('/api/auth/register/', {'email': 'x@example.com'}, format='json'))

    def test_password_forgot_20_per_hour(self):
        client = APIClient()
        for _ in range(20):
            res = client.post('/api/auth/password/forgot/', {'email': 'a@example.com'}, format='json')
            self.assertEqual(res.status_code, 200)
        self.assert_throttled(client.post('/api/auth/password/forgot/', {'email': 'a@example.com'}, format='json'))

    def test_check_in_30_per_minute_per_student(self):
        first = client_for(make_student('one@example.com', 2302001).user)
        second = client_for(make_student('two@example.com', 2302002).user)
        url = '/api/sessions/check-in/'
        body = {'code': '123456', 'session_id': str(uuid.uuid4()), 'device_id': 'phone'}
        for _ in range(30):
            self.assertEqual(first.post(url, body, format='json').status_code, 410)  # no such session
        self.assert_throttled(first.post(url, body, format='json'))
        self.assertEqual(second.post(url, body, format='json').status_code, 410)

    def test_requests_go_through_when_the_cache_is_down(self):
        down = mock.Mock(get=mock.Mock(side_effect=ConnectionError('Redis is down')))
        client = APIClient()
        body = {'email': 'nobody@example.com', 'password': 'wrong-pass'}
        with mock.patch.object(SimpleRateThrottle, 'cache', down), self.assertLogs('apps.users.throttles', 'ERROR'):
            for _ in range(12):
                self.assertEqual(client.post('/api/auth/login/', body, format='json').status_code, 401)
