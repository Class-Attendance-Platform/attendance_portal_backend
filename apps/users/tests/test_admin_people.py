"""Admin: people (API v2 section 2) — approvals, students, teachers."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.attendance.tests.helpers import (
    PASSWORD, client_for, make_admin, make_course_info, make_student, make_teacher, make_user,
)
from apps.faces.models import StudentFace
from apps.users.models import StudentProfile, TeacherProfile, User

TEMP = 'Temp-pass-4821'

STUDENT_ROW_KEYS = {
    'id', 'user_id', 'email', 'first_name', 'last_name', 'userName', 'student_id', 'current_level',
    'current_semester', 'is_verified', 'is_active', 'deleted', 'last_login', 'face_registered', 'semester',
}
TEACHER_ROW_KEYS = {
    'id', 'user_id', 'email', 'first_name', 'last_name', 'userName', 'employee_id', 'is_verified', 'is_active',
    'deleted', 'last_login', 'course_count',
}


def add_face(profile):
    return StudentFace.objects.create(
        student=profile, pose=StudentFace.Pose.STRAIGHT, crop_jpeg=b'x', embedding=b'x', engine='fake',
        consented_at=timezone.now(),
    )


def login(email, password=PASSWORD):
    return APIClient().post('/api/auth/login/', {'email': email, 'password': password}, format='json')


class AdminTestCase(TestCase):
    def setUp(self):
        cache.clear()  # login rate limit counts live in the cache
        self.admin = make_admin('admin@example.com')
        self.client = client_for(self.admin)


class OnlyAdminsTests(AdminTestCase):
    def test_others_are_refused(self):
        student = make_student('student@example.com', 2302001)
        teacher = make_teacher('teacher@example.com')
        for user in (student.user, teacher.user):
            client = client_for(user)
            for url in ('/api/admin/users/pending/', '/api/admin/students/', '/api/admin/teachers/'):
                self.assertEqual(client.get(url).status_code, 403, url)
            res = client.post(f'/api/admin/users/{student.user.id}/reset-password/', {'new_password': TEMP})
            self.assertEqual(res.status_code, 403)


class ApprovalTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.new_student = make_student('new@example.com', 2302005, is_verified=False)
        self.new_teacher = make_teacher('newteacher@example.com', is_verified=False)
        older = timezone.now() - timedelta(days=2)
        User.objects.filter(pk=self.new_teacher.user.pk).update(date_joined=older)

    def test_pending_list_oldest_first(self):
        make_student('approved@example.com', 2302006)
        make_student('rejected@example.com', 2302007, is_verified=False, deleted=True, is_active=False)
        make_user('another-admin@example.com', User.Role.ADMIN, is_verified=False)

        res = self.client.get('/api/admin/users/pending/')
        self.assertEqual(res.status_code, 200)
        users = res.data['users']
        self.assertEqual([u['email'] for u in users], ['newteacher@example.com', 'new@example.com'])
        self.assertEqual(users[0]['employee_id'], 'newteacher@example.com')
        self.assertNotIn('student_id', users[0])
        self.assertEqual(
            {k: users[1][k] for k in ('role', 'student_id', 'current_level', 'current_semester')},
            {'role': 'STUDENT', 'student_id': 2302005, 'current_level': 'Third', 'current_semester': 'I'},
        )
        self.assertEqual(set(users[1]), {'id', 'email', 'first_name', 'last_name', 'role', 'date_joined',
                                         'student_id', 'current_level', 'current_semester'})

    def test_verify(self):
        res = self.client.post(f'/api/admin/users/{self.new_student.user.id}/verify/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(User.objects.get(pk=self.new_student.user.pk).is_verified)
        self.assertEqual(login('new@example.com').status_code, 200)
        # The old app's PATCH still works
        res = self.client.patch(f'/api/admin/users/{self.new_teacher.user.id}/verify/')
        self.assertEqual(res.status_code, 200)

    def test_reject_soft_deletes_and_keeps_the_email_taken(self):
        add_face(self.new_student)
        res = self.client.post(f'/api/admin/users/{self.new_student.user.id}/reject/')
        self.assertEqual(res.status_code, 200)
        user = User.objects.get(pk=self.new_student.user.pk)
        self.assertTrue(user.deleted)
        self.assertFalse(user.is_active)
        self.assertFalse(StudentFace.objects.exists())
        self.assertEqual(login('new@example.com').data['code'], 'account_disabled')
        self.assertNotIn('new@example.com', [u['email'] for u in self.client.get('/api/admin/users/pending/').data['users']])

        res = APIClient().post('/api/auth/register/', {
            'role': 'STUDENT', 'email': 'new@example.com', 'password': 'Strong-pass-2026', 'first_name': 'A',
            'student_id': 2302099, 'current_level': 'Third', 'current_semester': 'I',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('email', res.data['errors'])

    def test_reject_refuses_admins(self):
        other = make_admin('other-admin@example.com')
        self.assertEqual(self.client.post(f'/api/admin/users/{other.id}/reject/').status_code, 404)


class ResetPasswordTests(AdminTestCase):
    def test_reset_password(self):
        student = make_student('student@example.com', 2302001)
        signed_in = login('student@example.com').data['refresh']
        res = self.client.post(f'/api/admin/users/{student.user.id}/reset-password/', {'new_password': TEMP},
                               format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['message'])
        self.assertEqual(login('student@example.com', TEMP).status_code, 200)
        refreshed = APIClient().post('/api/auth/refresh/', {'refresh': signed_in}, format='json')
        self.assertEqual(refreshed.status_code, 401)

    def test_password_is_validated(self):
        student = make_student('student@example.com', 2302001)
        res = self.client.post(f'/api/admin/users/{student.user.id}/reset-password/', {'new_password': '123'},
                               format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('new_password', res.data['errors'])
        self.assertTrue(res.data['message'])


class StudentListTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.ayesha = make_student('ayesha@example.com', 2302001, first_name='Ayesha', last_name='Rahman')
        self.tanvir = make_student('tanvir@example.com', 2302002, first_name='Tanvir', last_name='Hasan')
        StudentProfile.objects.filter(pk=self.tanvir.pk).update(current_level='Second', current_semester='II')
        self.gone = make_student('gone@example.com', 2202003, first_name='Gone', deleted=True, is_active=False)

    def students(self, query=''):
        res = self.client.get(f'/api/admin/students/{query}')
        self.assertEqual(res.status_code, 200)
        return res.data['students']

    def test_row(self):
        add_face(self.ayesha)
        semester = Semester.objects.create(level='Third', semester='I')
        classroom = Classroom.objects.create(name='Main', semester=semester)
        StudentClassroom.objects.create(student=self.ayesha, classroom=classroom)
        finished = Semester.objects.create(level='Second', semester='II', is_active=False)
        StudentClassroom.objects.create(student=self.tanvir,
                                        classroom=Classroom.objects.create(name='Main', semester=finished))

        rows = {r['student_id']: r for r in self.students()}
        self.assertEqual(set(rows), {2302001, 2302002})  # active only by default, ordered by student id
        row = rows[2302001]
        self.assertEqual(set(row), STUDENT_ROW_KEYS)
        self.assertEqual(row['id'], str(self.ayesha.id))
        self.assertEqual(row['user_id'], str(self.ayesha.user.id))
        self.assertEqual(row['userName'], 'Ayesha Rahman')
        self.assertTrue(row['face_registered'])
        self.assertEqual(row['semester'], {'id': str(semester.id), 'label': 'Level 3 · Term I'})
        self.assertEqual((row['is_verified'], row['is_active'], row['deleted'], row['last_login']),
                         (True, True, False, None))
        self.assertFalse(rows[2302002]['face_registered'])
        self.assertIsNone(rows[2302002]['semester'])  # finished semesters do not count

    def test_search(self):
        def ids(query):
            return [r['student_id'] for r in self.students(query)]

        self.assertEqual(ids('?search=ayesha'), [2302001])
        self.assertEqual(ids('?search=hasan'), [2302002])
        self.assertEqual(ids('?search=Ayesha%20Rahman'), [2302001])
        self.assertEqual(ids('?search=tanvir@example'), [2302002])
        self.assertEqual(ids('?search=2302'), [2302001, 2302002])
        self.assertEqual(ids('?search=02002'), [2302002])
        self.assertEqual(ids('?search=nobody'), [])

    def test_level_and_term_filters(self):
        self.assertEqual([r['student_id'] for r in self.students('?level=Second')], [2302002])
        self.assertEqual([r['student_id'] for r in self.students('?semester=I')], [2302001])
        self.assertEqual([r['student_id'] for r in self.students('?level=Third&semester=II')], [])

    def test_status(self):
        self.assertEqual([r['student_id'] for r in self.students('?status=deleted')], [2202003])
        res = self.client.get('/api/admin/students/?status=everything')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 'invalid_status')


class StudentCreateTests(AdminTestCase):
    def payload(self, **extra):
        return {
            'email': 'New.Student@Example.com', 'first_name': 'Nusrat', 'last_name': 'Jahan', 'student_id': 2302010,
            'current_level': 'Third', 'current_semester': 'I', 'password': TEMP, **extra,
        }

    def test_create_makes_an_approved_account(self):
        res = self.client.post('/api/admin/students/', self.payload(), format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(set(res.data['student']), STUDENT_ROW_KEYS)
        self.assertEqual(res.data['student']['email'], 'new.student@example.com')
        self.assertTrue(res.data['student']['is_verified'])
        self.assertEqual(login('new.student@example.com', TEMP).status_code, 200)

    def test_duplicates_and_missing_fields(self):
        make_student('taken@example.com', 2302011)
        for extra, field in (({'email': 'TAKEN@example.com'}, 'email'), ({'student_id': 2302011}, 'student_id'),
                             ({'current_level': ''}, 'current_level'), ({'password': 'abc'}, 'password')):
            res = self.client.post('/api/admin/students/', self.payload(**extra), format='json')
            self.assertEqual(res.status_code, 400, extra)
            self.assertIn(field, res.data['errors'])
            self.assertTrue(res.data['message'])
        self.assertFalse(User.objects.filter(email='new.student@example.com').exists())


class StudentDetailTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.student = make_student('student@example.com', 2302001, first_name='Ayesha', last_name='Rahman')
        self.url = f'/api/admin/students/{self.student.id}/'

    def test_get(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['student']['student_id'], 2302001)

    def test_patch_is_partial(self):
        res = self.client.patch(self.url, {'current_level': 'Fourth'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['student']['current_level'], 'Fourth')
        self.assertEqual(res.data['student']['current_semester'], 'I')
        self.assertEqual(res.data['student']['first_name'], 'Ayesha')

    def test_patch_email_and_ids(self):
        res = self.client.patch(self.url, {'email': 'Renamed@Example.com', 'student_id': 2302099,
                                           'first_name': 'Aisha'}, format='json')
        self.assertEqual(res.status_code, 200)
        user = User.objects.get(pk=self.student.user.pk)
        self.assertEqual((user.email, user.username, user.first_name), ('renamed@example.com', 'renamed@example.com', 'Aisha'))
        self.assertEqual(StudentProfile.objects.get(pk=self.student.pk).student_id, 2302099)
        self.assertEqual(login('renamed@example.com').status_code, 200)

    def test_patch_uniqueness(self):
        make_student('other@example.com', 2302002)
        for body, field in (({'email': 'OTHER@example.com'}, 'email'), ({'student_id': 2302002}, 'student_id')):
            res = self.client.patch(self.url, body, format='json')
            self.assertEqual(res.status_code, 400)
            self.assertIn(field, res.data['errors'])
        # Its own values are fine
        self.assertEqual(self.client.patch(self.url, {'email': 'student@example.com', 'student_id': 2302001},
                                           format='json').status_code, 200)

    def test_put_still_works_for_the_old_app(self):
        res = self.client.put(self.url, {'first_name': 'Old', 'last_name': 'App', 'faculty': 'X',
                                         'current_level': 'Third', 'current_semester': 'II'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['student']['current_semester'], 'II')

    def test_delete_and_restore(self):
        add_face(self.student)
        signed_in = login('student@example.com').data['refresh']
        self.assertEqual(self.client.delete(self.url).status_code, 200)
        user = User.objects.get(pk=self.student.user.pk)
        self.assertTrue(user.deleted)
        self.assertFalse(user.is_active)
        self.assertFalse(StudentFace.objects.exists())  # face data goes with it
        self.assertEqual(APIClient().post('/api/auth/refresh/', {'refresh': signed_in}, format='json').status_code, 401)
        self.assertEqual(self.client.get('/api/admin/students/').data['students'], [])
        self.assertEqual(self.client.patch(self.url, {'first_name': 'X'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 404)

        res = self.client.post(f'{self.url}restore/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['student']['deleted'])
        self.assertTrue(res.data['student']['is_active'])
        self.assertEqual(len(self.client.get('/api/admin/students/').data['students']), 1)
        self.assertEqual(login('student@example.com').status_code, 200)


class TeacherTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.teacher = make_teacher('rahim@example.com', first_name='Rahim', last_name='Uddin')
        TeacherProfile.objects.filter(pk=self.teacher.pk).update(employee_id='EMP-7')
        self.url = f'/api/admin/teachers/{self.teacher.id}/'

    def test_list_row_and_course_count(self):
        make_course_info(self.teacher, code='CSE301')
        gone = make_course_info(self.teacher, code='CSE302')
        CourseInfo.objects.filter(pk=gone.pk).update(deleted=True)
        hidden_course = make_course_info(self.teacher, code='CSE303')
        Course.objects.filter(pk=hidden_course.course_id).update(deleted=True)
        make_teacher('karim@example.com', first_name='Karim')

        res = self.client.get('/api/admin/teachers/')
        self.assertEqual(res.status_code, 200)
        rows = {r['email']: r for r in res.data['teachers']}
        self.assertEqual(set(rows['rahim@example.com']), TEACHER_ROW_KEYS)
        self.assertEqual(rows['rahim@example.com']['course_count'], 1)
        self.assertEqual(rows['karim@example.com']['course_count'], 0)
        self.assertEqual(rows['rahim@example.com']['employee_id'], 'EMP-7')

    def test_search_and_status(self):
        make_teacher('karim@example.com', first_name='Karim')
        emails = lambda q: [r['email'] for r in self.client.get(f'/api/admin/teachers/{q}').data['teachers']]  # noqa: E731
        self.assertEqual(emails('?search=emp-7'), ['rahim@example.com'])
        self.assertEqual(emails('?search=karim'), ['karim@example.com'])
        self.assertEqual(emails('?status=deleted'), [])

    def test_create(self):
        res = self.client.post('/api/admin/teachers/', {
            'email': 'new.teacher@example.com', 'first_name': 'New', 'last_name': 'Teacher',
            'employee_id': 'EMP-8', 'password': TEMP,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(set(res.data['teacher']), TEACHER_ROW_KEYS)
        self.assertTrue(res.data['teacher']['is_verified'])
        self.assertEqual(login('new.teacher@example.com', TEMP).status_code, 200)

        res = self.client.post('/api/admin/teachers/', {
            'email': 'x@example.com', 'first_name': 'X', 'employee_id': 'emp-8', 'password': TEMP,
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('employee_id', res.data['errors'])

    def test_patch_is_partial(self):
        res = self.client.patch(self.url, {'employee_id': 'EMP-70'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['teacher']['employee_id'], 'EMP-70')
        self.assertEqual(res.data['teacher']['first_name'], 'Rahim')

        make_teacher('karim@example.com')
        res = self.client.patch(self.url, {'employee_id': 'karim@example.com'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'A teacher with this employee ID already exists.')
        res = self.client.patch(self.url, {'employee_id': '  '}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_delete_and_restore(self):
        self.assertEqual(self.client.delete(self.url).status_code, 200)
        self.assertTrue(User.objects.get(pk=self.teacher.user.pk).deleted)
        self.assertEqual([r['email'] for r in self.client.get('/api/admin/teachers/?status=deleted').data['teachers']],
                         ['rahim@example.com'])
        res = self.client.post(f'{self.url}restore/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['teacher']['deleted'])
