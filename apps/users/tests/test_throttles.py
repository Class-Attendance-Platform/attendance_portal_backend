"""Request limits: login 10/min, register 5/hour, password forgot 5/hour (per IP), check-in 30/min per user."""
import uuid
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from apps.attendance.tests.helpers import client_for, make_student


class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def assert_throttled(self, res):
        self.assertEqual(res.status_code, 429)
        self.assertFalse(res.data['success'])
        self.assertEqual(res.data['code'], 'throttled')
        self.assertTrue(res.data['message'].startswith('Too many attempts. Please try again in'))
        self.assertIn('Retry-After', res.headers)

    def test_login_10_per_minute_per_ip(self):
        client = APIClient()
        body = {'email': 'nobody@example.com', 'password': 'wrong-pass'}
        for _ in range(10):
            self.assertEqual(client.post('/api/auth/login/', body, format='json').status_code, 401)
        self.assert_throttled(client.post('/api/auth/login/', body, format='json'))
        # Another address is counted on its own
        other = client.post('/api/auth/login/', body, format='json', REMOTE_ADDR='10.0.0.9')
        self.assertEqual(other.status_code, 401)

    def test_register_5_per_hour(self):
        client = APIClient()
        for _ in range(5):
            self.assertEqual(client.post('/api/auth/register/', {}, format='json').status_code, 400)
        self.assert_throttled(client.post('/api/auth/register/', {}, format='json'))

    def test_password_forgot_5_per_hour(self):
        client = APIClient()
        for _ in range(5):
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
