"""Every error answer carries a readable `message` (config/errors.py)."""
import json
import uuid

from django.test import RequestFactory, SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.attendance.tests.helpers import client_for, make_admin, make_student, make_teacher
from config.errors import first_error_message, json_server_error, throttled_message


class FirstErrorMessageTests(SimpleTestCase):
    def test_plain_words_never_field_colon_message(self):
        cases = [
            ({'student_id': ['This field is required.']}, 'Student ID is required.'),
            ({'first_name': ['This field may not be blank.']}, 'First name is required.'),
            ({'student_id': ['A valid integer is required.']}, 'Student ID must be a whole number.'),
            ({'last_name': ['Ensure this field has no more than 150 characters.']},
             'Ensure last name has no more than 150 characters.'),
            ({'mode': ['"X" is not a valid choice.']}, '"X" is not a valid mode.'),
            ({'non_field_errors': ['Something is off']}, 'Something is off.'),
            ({'email': ['An account with this email already exists.']}, 'An account with this email already exists.'),
            ({'students': [{}, {'email': ['Enter a valid email address.']}]}, 'Enter a valid email address.'),
            ({}, 'Please check the form and try again.'),
        ]
        for errors, expected in cases:
            self.assertEqual(first_error_message(errors), expected, errors)

    def test_throttled_message(self):
        self.assertEqual(throttled_message(41.2), 'Too many attempts. Please try again in 42 seconds.')
        self.assertEqual(throttled_message(1800), 'Too many attempts. Please try again in 30 minutes.')
        self.assertEqual(throttled_message(None), 'Too many attempts. Please try again later.')

    def test_server_error_view(self):
        res = json_server_error(RequestFactory().get('/api/x/'))
        self.assertEqual(res.status_code, 500)
        self.assertEqual(json.loads(res.content)['message'],
                         'Something went wrong on the server. Please try again.')


class ErrorResponseTests(TestCase):
    def assert_error(self, res, status, message=None, code=None):
        self.assertEqual(res.status_code, status)
        body = res.json()
        self.assertIs(body['success'], False)
        self.assertTrue(body['message'])
        if message:
            self.assertEqual(body['message'], message)
        if code:
            self.assertEqual(body['code'], code)
        return body

    def test_not_signed_in(self):
        self.assert_error(APIClient().get('/api/auth/me/'), 401, 'Please sign in to continue.', 'not_authenticated')

    def test_bad_token(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer not-a-token')
        self.assert_error(client.get('/api/auth/me/'), 401, 'Your sign-in has expired. Please sign in again.',
                          'token_not_valid')

    def test_wrong_role(self):
        student = make_student('student@example.com', 2302001)
        self.assert_error(client_for(student.user).get('/api/admin/students/'), 403, 'Only admins can do this.')

    def test_not_found(self):
        admin = make_admin('admin@example.com')
        self.assert_error(client_for(admin).get(f'/api/admin/students/{uuid.uuid4()}/'), 404,
                          'This item was not found.', 'not_found')

    def test_unknown_address(self):
        self.assert_error(APIClient().get('/api/no-such-thing/'), 404, 'This address does not exist.')

    def test_method_not_allowed(self):
        admin = make_admin('admin@example.com')
        body = self.assert_error(client_for(admin).put('/api/admin/students/'), 405)
        self.assertIn('PUT', body['message'])

    def test_field_errors_in_other_apps_carry_a_message(self):
        teacher = make_teacher('teacher@example.com')
        body = self.assert_error(client_for(teacher.user).post('/api/sessions/start/', {}, format='json'), 400)
        self.assertIn('course_info_id', body['errors'])
        self.assertEqual(body['message'], 'Course is required.')

    def test_malformed_json(self):
        admin = make_admin('admin@example.com')
        res = client_for(admin).generic('POST', '/api/admin/students/', '{oops', content_type='application/json')
        self.assert_error(res, 400)
