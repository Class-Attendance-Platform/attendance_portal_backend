import importlib
from io import StringIO

from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import TestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.attendance.tests.helpers import make_admin, make_student, make_teacher, make_user
from apps.users.models import User


def run(*args):
    out = StringIO()
    call_command('cleanup_broken_signups', *args, stdout=out)
    return out.getvalue()


class CleanupBrokenSignupsTests(TestCase):
    def setUp(self):
        self.broken_student = make_user('broken-student@example.com', User.Role.STUDENT, is_verified=False)
        self.broken_teacher = make_user('broken-teacher@example.com', User.Role.TEACHER, is_verified=False)
        make_student('fine@example.com', 2302001)
        make_teacher('teacher@example.com')
        make_user('admin-without-profile@example.com', User.Role.ADMIN)  # admins are not sign-ups
        make_admin('admin@example.com')

    def test_dry_run_lists_them_and_changes_nothing(self):
        out = run()
        self.assertIn('Accounts without a profile: 2', out)
        self.assertIn('broken-student@example.com (role STUDENT', out)
        self.assertIn('broken-teacher@example.com (role TEACHER', out)
        self.assertNotIn('fine@example.com', out)
        self.assertIn('Dry run', out)
        self.assertEqual(User.objects.count(), 6)

    def test_apply_removes_only_them(self):
        RefreshToken.for_user(self.broken_student)
        out = run('--apply')
        self.assertIn('Removed 2 account(s).', out)
        self.assertEqual(
            sorted(User.objects.values_list('email', flat=True)),
            ['admin-without-profile@example.com', 'admin@example.com', 'fine@example.com', 'teacher@example.com'],
        )
        self.assertEqual(BlacklistedToken.objects.count(), 1)
        self.assertIn('nothing to do', run('--apply'))


class MarkExistingUsersVerifiedMigrationTests(TestCase):
    def test_every_existing_user_becomes_verified(self):
        make_student('pending@example.com', 2302001, is_verified=False)
        make_teacher('teacher@example.com', is_verified=False)
        make_admin('admin@example.com')
        migration = importlib.import_module('apps.users.migrations.0002_mark_existing_users_verified')
        migration.mark_existing_users_verified(django_apps, None)
        self.assertFalse(User.objects.filter(is_verified=False).exists())
