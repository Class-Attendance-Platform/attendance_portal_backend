from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import CourseInfo
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.tests.helpers import PASSWORD, make_admin, make_course_info, make_student, make_teacher
from apps.users.models import AdminProfile, StudentProfile, User


def run(*args):
    out = StringIO()
    call_command('remove_user', *args, stdout=out)
    return out.getvalue()


class RemoveUserTests(TestCase):
    def setUp(self):
        self.new_admin = make_admin('new-admin@example.com')
        self.old_admin = make_admin('old-admin@example.com')

    def test_dry_run_shows_what_goes_and_changes_nothing(self):
        out = run('old-admin@example.com')
        self.assertIn('Account: old-admin@example.com (role ADMIN', out)
        self.assertIn('admin profiles: 1', out)
        self.assertIn('users: 1', out)
        self.assertIn('Dry run', out)
        self.assertTrue(User.objects.filter(email='old-admin@example.com').exists())
        self.assertEqual(AdminProfile.objects.count(), 2)

    def test_apply_removes_the_account_and_its_profile(self):
        out = run('old-admin@example.com', '--apply')
        self.assertIn('Removed old-admin@example.com', out)
        self.assertFalse(User.objects.filter(email='old-admin@example.com').exists())
        self.assertEqual(list(AdminProfile.objects.values_list('user__email', flat=True)), ['new-admin@example.com'])

    def test_email_capitals_do_not_matter(self):
        run('  Old-Admin@Example.COM ', '--apply')
        self.assertFalse(User.objects.filter(email='old-admin@example.com').exists())

    def test_refuses_to_remove_the_last_active_admin(self):
        run('old-admin@example.com', '--apply')
        with self.assertRaisesMessage(CommandError, 'only active admin'):
            run('new-admin@example.com', '--apply')
        self.assertTrue(User.objects.filter(email='new-admin@example.com').exists())

    def test_an_inactive_admin_does_not_count_as_another_admin(self):
        User.objects.filter(email='old-admin@example.com').update(is_active=False)
        with self.assertRaisesMessage(CommandError, 'only active admin'):
            run('new-admin@example.com')

    def test_old_login_tokens_stop_working(self):
        client = APIClient()
        login = client.post('/api/auth/login/', {'email': 'old-admin@example.com', 'password': PASSWORD}, format='json')
        refresh = login.json()['refresh']

        out = run('old-admin@example.com', '--apply')
        self.assertIn('Blocked 1 login token', out)
        response = client.post('/api/auth/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_tokens_already_blocked_by_a_refresh_are_fine(self):
        client = APIClient()
        login = client.post('/api/auth/login/', {'email': 'old-admin@example.com', 'password': PASSWORD}, format='json')
        rotated = client.post('/api/auth/refresh/', {'refresh': login.json()['refresh']}, format='json')
        newest = rotated.json()['refresh']  # the first token is blocked by this refresh already

        run('old-admin@example.com', '--apply')
        response = client.post('/api/auth/refresh/', {'refresh': newest}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_unknown_email(self):
        with self.assertRaisesMessage(CommandError, 'No account with the email nobody@example.com'):
            run('nobody@example.com', '--apply')

    def test_student_dry_run_lists_their_attendance(self):
        teacher = make_teacher('teacher@example.com')
        student = make_student('student@example.com', 2302001)
        course_info = make_course_info(teacher, students=[student])
        session = AttendanceSession.objects.create(
            course_info=course_info, date=timezone.localdate(), mode=AttendanceSession.Mode.QR_ONLINE, is_active=False
        )
        AttendanceLog.objects.create(
            session=session, course_info=course_info, student=student, date=timezone.localdate()
        )

        out = run('student@example.com')
        self.assertIn('student profiles: 1', out)
        self.assertIn('attendance logs: 1', out)
        self.assertEqual(AttendanceLog.objects.count(), 1)

        run('student@example.com', '--apply')
        self.assertFalse(StudentProfile.objects.exists())
        self.assertFalse(AttendanceLog.objects.exists())
        self.assertTrue(AttendanceSession.objects.exists())  # the class itself stays

    def test_teacher_classes_stay_without_a_teacher(self):
        teacher = make_teacher('teacher@example.com')
        make_course_info(teacher)

        out = run('teacher@example.com')
        self.assertIn('Kept, but no longer linked to this account:', out)
        self.assertIn('course infos: 1 (their teacher is cleared)', out)

        run('teacher@example.com', '--apply')
        self.assertEqual(CourseInfo.objects.count(), 1)
        self.assertIsNone(CourseInfo.objects.get().teacher)
