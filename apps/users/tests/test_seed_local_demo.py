"""seed_local_demo: rich local demo data (SQLite only, dry run by default, safe to run again)."""
from collections import Counter
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from apps.academic.models import CourseInfo, Semester, StudentClassroom
from apps.academic.stats import course_numbers
from apps.attendance.models import AttendanceChange, AttendanceLog, AttendanceSession
from apps.users.management.commands.seed_local_demo import DEMO_PASSWORD
from apps.users.models import StudentProfile, TeacherProfile, User


def seed(*args):
    out = StringIO()
    call_command('seed_local_demo', *args, stdout=out)
    return out.getvalue()


class SeedLocalDemoTests(TestCase):
    def test_dry_run_writes_nothing(self):
        output = seed()
        self.assertIn('create  admin@demo.local (admin)', output)
        self.assertIn('Dry run: nothing written', output)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Semester.objects.exists())

    def test_refuses_anything_but_sqlite(self):
        with mock.patch('apps.users.management.commands.seed_local_demo.connection') as conn:
            conn.vendor = 'postgresql'
            with self.assertRaises(CommandError):
                call_command('seed_local_demo', '--apply', stdout=StringIO())
        self.assertFalse(User.objects.exists())

    def test_apply_makes_rich_demo_data(self):
        output = seed('--apply')
        self.assertIn(f'password {DEMO_PASSWORD}', output)

        # Accounts: the demo logins, 12 approved students, 2 teachers, 2 pending, 1 deleted
        admin = User.objects.get(email='admin@demo.local')
        self.assertTrue(admin.is_superuser and admin.role == 'ADMIN' and admin.check_password(DEMO_PASSWORD))
        approved = StudentProfile.objects.filter(user__is_verified=True, user__deleted=False)
        self.assertEqual(approved.count(), 12)
        self.assertTrue(User.objects.get(email='student2302001@demo.local').check_password(DEMO_PASSWORD))
        self.assertEqual(TeacherProfile.objects.filter(user__is_verified=True).count(), 2)
        pending = User.objects.filter(is_verified=False)
        self.assertEqual(sorted(pending.values_list('email', 'role')),
                         [('pending.student@demo.local', 'STUDENT'), ('pending.teacher@demo.local', 'TEACHER')])
        self.assertTrue(StudentProfile.objects.filter(user__email='pending.student@demo.local').exists())
        deleted = User.objects.get(email='student2302013@demo.local')
        self.assertEqual((deleted.deleted, deleted.is_active), (True, False))
        self.assertTrue(AttendanceLog.objects.filter(student__user=deleted).exists())  # history kept

        # Semesters: one active (3 courses), one finished (its students promoted)
        current = Semester.objects.get(is_active=True)
        self.assertEqual((current.label, current.deleted), ('Level 3 · Term I · 2025-26', False))
        finished = Semester.objects.get(is_active=False)
        self.assertEqual(finished.label, 'Level 2 · Term II · 2024-25')
        self.assertEqual(sorted(CourseInfo.objects.filter(semester=current).values_list('course__code', flat=True)),
                         ['CSE301', 'CSE303', 'CSE305'])
        self.assertEqual(CourseInfo.objects.filter(semester=finished).count(), 2)
        self.assertFalse(StudentClassroom.objects.filter(classroom__semester=finished, left_at__isnull=True).exists())

        # Late joiner: counted from the day they joined
        late = StudentClassroom.objects.get(classroom__semester=current, student__student_id=2302012)
        self.assertIsNotNone(late.joined_at)
        self.assertFalse(AttendanceLog.objects.filter(student=late.student, date__lt=late.joined_at).exists())

        # About 10 past class dates per current course, mixed statuses and methods
        cse301 = CourseInfo.objects.get(semester=current, course__code='CSE301')
        numbers = course_numbers([cse301])[cse301.id]
        self.assertEqual(numbers.student_count, 12)  # deleted account left out, late joiner in
        self.assertEqual(numbers.classes_held, 10)
        self.assertTrue(all(day < timezone.localdate() for day in numbers.class_dates))
        late_numbers = numbers.students[late.student_id]
        self.assertLess(late_numbers.held, numbers.classes_held)
        statuses = Counter(AttendanceLog.objects.values_list('status', flat=True))
        self.assertGreater(statuses['ABSENT'], 0)
        self.assertGreater(statuses['PRESENT'], statuses['ABSENT'])
        methods = set(AttendanceLog.objects.filter(status='PRESENT').values_list('method', flat=True))
        self.assertLessEqual({'QR', 'CODE', 'FACE', 'TEACHER'}, methods)
        deliveries = set(AttendanceSession.objects.values_list('delivery', flat=True))
        self.assertEqual(deliveries, {'IN_CLASS', 'ONLINE'})
        self.assertFalse(AttendanceSession.objects.filter(is_active=True).exists())
        per_day = Counter(AttendanceSession.objects.filter(course_info=cse301).values_list('date', flat=True))
        self.assertIn(2, per_day.values())  # a day with two sessions
        self.assertTrue(AttendanceLog.objects.filter(session__isnull=True, method='TEACHER').exists())  # roll call
        self.assertEqual(AttendanceChange.objects.count(), 2)
        self.assertGreater(numbers.below_min_count, 0)

    def test_reuses_the_older_demo_semester(self):
        # The pre-v2 seed made Level 3 Term I without a session, CSE301 and three students
        older = Semester.objects.create(level='Third', semester='I')
        seed('--apply')
        older.refresh_from_db()
        self.assertEqual(older.session, '2025-26')
        self.assertEqual(Semester.objects.filter(level='Third', is_active=True).count(), 1)
        self.assertEqual(CourseInfo.objects.filter(semester=older).count(), 3)

    def test_running_again_adds_nothing(self):
        seed('--apply')
        counts = (User.objects.count(), Semester.objects.count(), CourseInfo.objects.count(),
                  StudentClassroom.objects.count(), AttendanceSession.objects.count(), AttendanceLog.objects.count())
        output = seed('--apply')
        self.assertIn('exists  teacher2@demo.local (teacher)', output)
        self.assertEqual(counts, (User.objects.count(), Semester.objects.count(), CourseInfo.objects.count(),
                                  StudentClassroom.objects.count(), AttendanceSession.objects.count(),
                                  AttendanceLog.objects.count()))
