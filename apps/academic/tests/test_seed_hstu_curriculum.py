"""Tests for seed_hstu_curriculum management command."""
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.users.models import StudentProfile, TeacherProfile, User


def seed(*args):
    out = StringIO()
    call_command('seed_hstu_curriculum', *args, stdout=out)
    return out.getvalue()


class SeedHstuCurriculumTests(TestCase):
    def test_dry_run_writes_nothing(self):
        output = seed()
        self.assertIn('[DRY RUN] No changes were written', output)
        self.assertIn('Curriculum courses to seed: 107', output)
        self.assertIn('Pending approval users:     3 teachers, 5 students', output)
        self.assertFalse(Course.objects.exists())
        self.assertFalse(User.objects.exists())
        self.assertFalse(Semester.objects.exists())

    def test_refuses_remote_database_without_allow_remote(self):
        with mock.patch('apps.academic.management.commands.seed_hstu_curriculum.connection') as conn:
            conn.vendor = 'postgresql'
            conn.settings_dict = {'NAME': 'remote_neon_db'}
            with self.assertRaises(CommandError) as ctx:
                call_command('seed_hstu_curriculum', '--apply', stdout=StringIO())
            self.assertIn('Safety protection', str(ctx.exception))
        self.assertFalse(Course.objects.exists())

    def test_apply_seeds_full_curriculum_teachers_and_students(self):
        output = seed('--apply')
        self.assertIn('Successfully seeded HSTU CSE 2017 Curriculum Data', output)
        self.assertIn('Courses: 107 created', output)

        # 107 curriculum courses
        self.assertEqual(Course.objects.count(), 107)

        # Verify key courses and their credits
        c101 = Course.objects.get(code='CSE 101')
        self.assertEqual(c101.title, 'Fundamentals of Computer and Computing')
        self.assertEqual(c101.credits, Course.Credits.CREDIT_2_00)

        c102 = Course.objects.get(code='CSE 102')
        self.assertEqual(c102.title, 'Fundamentals of Computer and Computing Sessional')
        self.assertEqual(c102.credits, Course.Credits.CREDIT_0_75)

        c452 = Course.objects.get(code='CSE 452')
        self.assertEqual(c452.title, 'Project and Thesis Sessional')
        self.assertEqual(c452.credits, Course.Credits.CREDIT_3_00)

        # Teachers (24 faculty members)
        self.assertEqual(TeacherProfile.objects.filter(user__is_verified=True).count(), 24)
        self.assertEqual(TeacherProfile.objects.filter(user__is_verified=False).count(), 3)
        mamun = User.objects.get(email='mamun@hstu.ac.bd')
        self.assertEqual(mamun.first_name, 'Dr. Md. Abdulla Al')
        self.assertEqual(mamun.last_name, 'Mamun')
        self.assertTrue(mamun.check_password('HstuCse@2026'))

        # Semesters & Classrooms (4 batches)
        self.assertEqual(Semester.objects.count(), 4)
        self.assertEqual(Classroom.objects.count(), 4)

        # Students (15 per batch * 4 batches = 60 students)
        self.assertEqual(StudentProfile.objects.filter(user__is_verified=True).count(), 60)
        self.assertEqual(StudentProfile.objects.filter(user__is_verified=False).count(), 5)
        self.assertEqual(StudentClassroom.objects.count(), 60)

        # CourseInfos created for active semester courses
        self.assertTrue(CourseInfo.objects.filter(course__code='CSE 101').exists())
        self.assertTrue(CourseInfo.objects.filter(course__code='CSE 201').exists())
        self.assertTrue(CourseInfo.objects.filter(course__code='CSE 303').exists())
        self.assertTrue(CourseInfo.objects.filter(course__code='CSE 403').exists())

        # Pending approval accounts
        pending_users = User.objects.filter(is_verified=False)
        self.assertEqual(pending_users.count(), 8)
        self.assertEqual(pending_users.filter(role=User.Role.TEACHER).count(), 3)
        self.assertEqual(pending_users.filter(role=User.Role.STUDENT).count(), 5)

        # Check a pending teacher and student
        pt = User.objects.get(email='pending.teacher1@hstu.ac.bd')
        self.assertFalse(pt.is_verified)
        self.assertEqual(pt.teacher_profile.employee_id, 'EMP-CSE-025')

        ps = User.objects.get(email='pending.student2402016@hstu.ac.bd')
        self.assertFalse(ps.is_verified)
        self.assertEqual(ps.student_profile.student_id, 2402016)
        self.assertEqual(ps.student_profile.current_level, 'First')
        self.assertEqual(ps.student_profile.current_semester, 'I')


    def test_running_again_is_idempotent(self):
        seed('--apply')
        counts_before = (
            Course.objects.count(),
            TeacherProfile.objects.count(),
            StudentProfile.objects.count(),
            Semester.objects.count(),
            CourseInfo.objects.count(),
            StudentClassroom.objects.count(),
        )
        output = seed('--apply')
        self.assertIn('0 created, 107 existing verified', output)
        counts_after = (
            Course.objects.count(),
            TeacherProfile.objects.count(),
            StudentProfile.objects.count(),
            Semester.objects.count(),
            CourseInfo.objects.count(),
            StudentClassroom.objects.count(),
        )
        self.assertEqual(counts_before, counts_after)
