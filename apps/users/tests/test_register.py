from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.attendance.tests.helpers import make_student, make_teacher
from apps.faces.engines import FaceEngineUnavailable
from apps.faces.models import StudentFace
from apps.faces.tests.test_faces import FakeEngine, face, person
from apps.users.constants import DEPARTMENT, FACULTY
from apps.users.models import StudentProfile, TeacherProfile, User

STRONG = 'Strong-pass-2026'


class PublicRegisterTests(TestCase):
    def setUp(self):
        cache.clear()  # sign-up rate limit counts live in the cache
        self.client = APIClient()
        self.engine = FakeEngine()
        patcher = mock.patch('apps.faces.services.get_engine', return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.colors = iter((i, 0, 0) for i in range(1, 250))

    def photo(self, *faces):
        return self.engine.photo(next(self.colors), list(faces))

    def valid_face_fields(self, n=1):
        return {
            'consent': 'true',
            'straight': self.photo(face(person(n, 0.1, 1))),
            'left': self.photo(face(person(n, 0.1, 2))),
            'right': self.photo(face(person(n, 0.1, 3))),
        }

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

    def student(self, with_face=False, person_id=1, **extra):
        base = {'student_id': 2302001, 'current_level': 'Third', 'current_semester': 'I'}
        if with_face:
            base.update(self.valid_face_fields(person_id))
        return self.payload('STUDENT', **{**base, **extra})

    def register(self, data, fmt='json'):
        return self.client.post('/api/auth/register/', data, format=fmt)

    def check_register(self, data):
        return self.client.post('/api/auth/register/check/', data, format='json')

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

    def test_student_sign_up_requires_face_consent_and_three_photos(self):
        # Missing consent and photos -> 400, no account created
        res = self.register(self.student())
        self.assertEqual(res.status_code, 400)
        self.assertIn('consent', res.data['message'])
        self.assertFalse(User.objects.exists())

        # Consent given, missing left/right photos -> 400, no account created
        partial = self.student(consent='true', straight=self.photo(face(person(1))))
        res = self.register(partial, fmt='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Missing photo', res.data['message'])
        self.assertFalse(User.objects.exists())

    def test_student_sign_up_with_invalid_or_duplicate_face_leaves_no_account(self):
        # No face in one photo
        bad_pose = self.student(
            consent='true',
            straight=self.photo(face(person(1))),
            left=self.photo(),
            right=self.photo(face(person(1))),
        )
        res = self.register(bad_pose, fmt='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertIn('No face found in the left photo', res.data['message'])
        self.assertFalse(User.objects.exists())
        self.assertFalse(StudentFace.objects.exists())

        # Duplicate face belonging to another student
        existing = make_student('existing@example.com', 2302900)
        other_res = self.client.post(
            '/api/auth/register/',
            self.student(with_face=True, person_id=5, email='first@example.com', student_id=2302901),
            format='multipart',
        )
        self.assertEqual(other_res.status_code, 201)
        dup_res = self.register(
            self.student(with_face=True, person_id=5, email='dup@example.com', student_id=2302902),
            fmt='multipart',
        )
        self.assertEqual(dup_res.status_code, 409)
        self.assertIn('already registered', dup_res.data['message'])
        self.assertFalse(User.objects.filter(email='dup@example.com').exists())
        self.assertEqual(StudentFace.objects.count(), 3)
        self.assertTrue(existing.pk)

    def test_student_sign_up_when_face_engine_unavailable_leaves_no_account(self):
        with mock.patch('apps.faces.services.get_engine', side_effect=FaceEngineUnavailable('missing models')):
            res = self.register(self.student(with_face=True), fmt='multipart')
        self.assertEqual(res.status_code, 503)
        self.assertFalse(User.objects.exists())

    def test_student_sign_up_registers_face_marks_verified_and_returns_tokens(self):
        res = self.register(self.student(with_face=True, email='New.Student@Example.COM'), fmt='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['success'])
        self.assertEqual(res.data['status'], 'approved')
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(res.data['user']['email'], 'new.student@example.com')

        user = User.objects.get()
        self.assertEqual(user.email, 'new.student@example.com')  # stored lower case
        self.assertEqual(user.username, 'new.student@example.com')
        self.assertTrue(user.is_verified)
        self.assertEqual((user.faculty, user.department), (FACULTY, DEPARTMENT))
        self.assertEqual(user.student_profile.student_id, 2302001)
        self.assertEqual(user.student_profile.current_level, 'Third')
        self.assertEqual(StudentFace.objects.filter(student=user.student_profile).count(), 3)

        # Returned access token works immediately and shows face registered
        authed = APIClient()
        authed.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        face_res = authed.get('/api/faces/me/')
        self.assertEqual(face_res.status_code, 200)
        self.assertTrue(face_res.data['registered'])
        self.assertCountEqual(face_res.data['poses'], ['STRAIGHT', 'LEFT', 'RIGHT'])

    def test_teacher_sign_up_is_verified_immediately_and_returns_tokens(self):
        res = self.register(self.payload('TEACHER', employee_id=' EMP-1 '))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'approved')
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(TeacherProfile.objects.get().employee_id, 'EMP-1')
        user = User.objects.get()
        self.assertTrue(user.is_verified)

        # Can also sign in immediately with password
        login_res = self.client.post('/api/auth/login/', {'email': 'teacher@example.com', 'password': STRONG}, format='json')
        self.assertEqual(login_res.status_code, 200)

    def test_register_check_validates_without_creating_accounts(self):
        # Valid student details pass check without face photos and create nothing
        res = self.check_register(self.student())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {'success': True})
        self.assertFalse(User.objects.exists())

        # Duplicate student ID is caught by check
        make_student('other@example.com', 2302001)
        dup = self.check_register(self.student())
        self.assertEqual(dup.status_code, 400)
        self.assertIn('student_id', dup.data['errors'])

    def test_faculty_and_department_are_set_by_the_server(self):
        self.register(self.payload('TEACHER', employee_id='EMP-1', faculty='AGRICULTURE', department='X'))
        user = User.objects.get()
        self.assertEqual((user.faculty, user.department), (FACULTY, DEPARTMENT))

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
            res = self.register(self.student(with_face=True), fmt='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.data['message'])
        self.assertFalse(User.objects.exists())
        self.assertFalse(StudentFace.objects.exists())
