"""
Fills a LOCAL SQLite database with demo logins and one course, for previews.

    python manage.py migrate --settings=config.settings.test
    python manage.py seed_local_demo --settings=config.settings.test           # dry run
    python manage.py seed_local_demo --settings=config.settings.test --apply   # write

Refuses to run against anything but SQLite, so it can never touch the real database.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.users.models import AdminProfile, StudentProfile, TeacherProfile, User

DEMO_PASSWORD = 'Demo-pass-2026'
DOMAIN = 'demo.local'
STUDENTS = [
    (2302001, 'Ayesha', 'Rahman'),
    (2302002, 'Tanvir', 'Hasan'),
    (2302003, 'Nusrat', 'Jahan'),
]


class Command(BaseCommand):
    help = 'Create demo logins and a course in a local SQLite database (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually write the demo data.')

    def handle(self, *args, **options):
        if connection.vendor != 'sqlite':
            raise CommandError('Refusing to run: this command only works on a local SQLite database.')

        logins = [f'admin@{DOMAIN}', f'teacher@{DOMAIN}'] + [f'student{sid}@{DOMAIN}' for sid, _, _ in STUDENTS]
        existing = set(User.objects.filter(email__in=logins).values_list('email', flat=True))
        self.stdout.write(f'Database: {connection.settings_dict["NAME"]}')
        for email in logins:
            self.stdout.write(f'  {"exists " if email in existing else "create "} {email}')
        self.stdout.write(f'  course CSE301 with {len(STUDENTS)} students')

        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing written. Add --apply to write.'))
            return

        with transaction.atomic():
            admin = self._user(f'admin@{DOMAIN}', User.Role.ADMIN, 'Demo', 'Admin', is_staff=True, is_superuser=True)
            AdminProfile.objects.get_or_create(user=admin)
            teacher_user = self._user(f'teacher@{DOMAIN}', User.Role.TEACHER, 'Demo', 'Teacher')
            teacher, _ = TeacherProfile.objects.get_or_create(user=teacher_user, defaults={'employee_id': 'EMP-DEMO'})

            semester, _ = Semester.objects.get_or_create(
                level='Third', semester='I', defaults={'is_active': True, 'session': '2025-26'},
            )
            course, _ = Course.objects.get_or_create(code='CSE301', defaults={'title': 'Software Engineering'})
            classroom, _ = Classroom.objects.get_or_create(semester=semester, name='Main')
            CourseInfo.objects.get_or_create(course=course, teacher=teacher, semester=semester, classroom=classroom)

            for sid, first, last in STUDENTS:
                user = self._user(f'student{sid}@{DOMAIN}', User.Role.STUDENT, first, last)
                profile, _ = StudentProfile.objects.get_or_create(
                    user=user, defaults={'student_id': sid, 'current_level': 'Third', 'current_semester': 'I'}
                )
                StudentClassroom.objects.get_or_create(student=profile, classroom=classroom)

        self.stdout.write(self.style.SUCCESS(f'Done. Every demo login uses the password {DEMO_PASSWORD}'))

    def _user(self, email, role, first, last, **extra):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'username': email, 'role': role, 'first_name': first, 'last_name': last,
                      'is_verified': True, **extra},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user
