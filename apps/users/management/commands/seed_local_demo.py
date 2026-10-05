"""
Fills a LOCAL SQLite database with demo data for screen previews (dry run unless --apply).

    python manage.py migrate --settings=config.settings.test
    python manage.py seed_local_demo --settings=config.settings.test           # dry run
    python manage.py seed_local_demo --settings=config.settings.test --apply   # write

Refuses to run against anything but SQLite, so it can never touch the real database.

What it makes (every login uses DEMO_PASSWORD):
- admin@demo.local; teacher@demo.local and teacher2@demo.local
- 12 approved students student2302001..012@demo.local (2302012 joined the class late)
- an active semester Level 3 · Term I · 2025-26 (started six weeks ago) with CSE301, CSE303
  (teacher@) and CSE305 (teacher2@), about 10 past class dates each: QR, typed code, face,
  online and roll-call classes, one day with two sessions, two teacher corrections
- a finished semester Level 2 · Term II · 2024-25 (CSE201, CSE203) whose students were promoted
- waiting for approval: pending.student@demo.local, pending.teacher@demo.local
- a soft-deleted student: student2302013@demo.local
Dates are counted back from today. Running it again keeps what exists; attendance is only
added to courses that have none.
"""
import random
import secrets
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.academic.models import Course, CourseInfo, Semester, StudentClassroom
from apps.academic.services import create_semester, main_classroom
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.services import commit_session_to_db, correct_attendance, enrolled_memberships, roll_call
from apps.faces.services import save_face_attendance
from apps.users.constants import DEPARTMENT, FACULTY
from apps.users.models import AdminProfile, StudentProfile, TeacherProfile, User
from apps.users.services import deactivate_user

DEMO_PASSWORD = 'Demo-pass-2026'
DOMAIN = 'demo.local'

# (student id, first name, last name, how often they come to class)
STUDENTS = [
    (2302001, 'Ayesha', 'Rahman', 0.95),
    (2302002, 'Tanvir', 'Hasan', 0.68),
    (2302003, 'Nusrat', 'Jahan', 0.88),
    (2302004, 'Rakib', 'Hossain', 0.9),
    (2302005, 'Sadia', 'Islam', 0.55),
    (2302006, 'Mehedi', 'Hasan', 0.82),
    (2302007, 'Farzana', 'Akter', 1.0),
    (2302008, 'Arif', 'Chowdhury', 0.78),
    (2302009, 'Tasnim', 'Ahmed', 0.92),
    (2302010, 'Imran', 'Kabir', 0.45),
    (2302011, 'Sumaiya', 'Khatun', 0.86),
]
LATE_JOINER = (2302012, 'Rafiq', 'Uddin', 0.9)        # joined the current semester LATE_JOIN_DAYS ago
DELETED_STUDENT = (2302013, 'Shakil', 'Ahmed', 0.7)   # soft-deleted at the end (history kept)
PENDING_STUDENT = (2302014, 'Nadia', 'Sultana', f'pending.student@{DOMAIN}')
# (email, first name, last name, employee id)
TEACHERS = [
    (f'teacher@{DOMAIN}', 'Demo', 'Teacher', 'EMP-DEMO'),
    (f'teacher2@{DOMAIN}', 'Farhana', 'Akter', 'EMP-DEMO-2'),
]
PENDING_TEACHER = (f'pending.teacher@{DOMAIN}', 'Kamal', 'Hossain', 'EMP-PENDING')

SEMESTER_WEEKS = 6     # the current semester started six weeks ago (the students were promoted then)
LATE_JOIN_DAYS = 17
CURRENT = ('Third', 'I', '2025-26')
PREVIOUS = ('Second', 'II', '2024-25')
# (code, title, teacher index, weekdays (Monday = 0), class dates, class time)
CURRENT_COURSES = [
    ('CSE301', 'Software Engineering', 0, (6, 1), 10, time(10, 0)),
    ('CSE303', 'Database Systems', 0, (0, 2), 10, time(11, 30)),
    ('CSE305', 'Computer Networks', 1, (3, 6), 9, time(14, 0)),
]
PREVIOUS_COURSES = [
    ('CSE201', 'Data Structures', 0, (6, 2), 8, time(10, 0)),
    ('CSE203', 'Digital Logic Design', 1, (0, 3), 8, time(12, 0)),
]
# How each class date is taken, in turn: live QR session (in class or online, the students scan
# the QR or type the code), face photo, roll call, or a QR session followed by a face photo.
CLASS_KINDS = ['QR', 'FACE', 'QR', 'ROLL_CALL', 'ONLINE', 'QR', 'QR+FACE', 'QR', 'FACE', 'QR']


def find_semester(level, term, session):
    """The demo semester; for the current one also Level 3 Term I without a session (the older seed's)."""
    found = Semester.objects.filter(level=level, semester=term, deleted=False)
    older = found.filter(session='').first() if (level, term, session) == CURRENT else None
    return found.filter(session=session).first() or older


def student_email(student_id):
    return f'student{student_id}@{DOMAIN}'


def past_dates(weekdays, count, last_day, first_day):
    """The latest `count` dates on these weekdays between first_day and last_day, oldest first."""
    days, day = [], last_day
    while len(days) < count and day >= first_day:
        if day.weekday() in weekdays:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


class Command(BaseCommand):
    help = 'Fill a local SQLite database with demo data for previews (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually write the demo data.')

    def handle(self, *args, **options):
        if connection.vendor != 'sqlite':
            raise CommandError('Refusing to run: this command only works on a local SQLite database.')

        today = timezone.localdate()
        self.stdout.write(f'Database: {connection.settings_dict["NAME"]}')
        self._print_plan(today)
        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing written. Add --apply to write.'))
            return

        with transaction.atomic():
            self._seed(today)

        self.stdout.write(self.style.SUCCESS(f'Done. Every demo login uses the password {DEMO_PASSWORD}'))
        self.stdout.write(f'  admin@{DOMAIN}, teacher@{DOMAIN}, teacher2@{DOMAIN}, '
                          f'{student_email(2302001)} ... {student_email(LATE_JOINER[0])}')
        self.stdout.write(f'  waiting for approval (cannot sign in yet): {PENDING_STUDENT[3]}, {PENDING_TEACHER[0]}')
        self.stdout.write(f'  soft-deleted: {student_email(DELETED_STUDENT[0])}')

    # ── Dry run ───────────────────────────────────────────────────────────────

    def _logins(self):
        """(email, what it is) for every account this command makes."""
        rows = [(f'admin@{DOMAIN}', 'admin')]
        rows += [(email, 'teacher') for email, _, _, _ in TEACHERS]
        rows += [(student_email(sid), 'student') for sid, _, _, _ in STUDENTS]
        rows += [
            (student_email(LATE_JOINER[0]), 'student, joined late'),
            (student_email(DELETED_STUDENT[0]), 'student, soft-deleted'),
            (PENDING_STUDENT[3], 'student, waiting for approval'),
            (PENDING_TEACHER[0], 'teacher, waiting for approval'),
        ]
        return rows

    def _print_plan(self, today):
        logins = self._logins()
        existing = set(User.objects.filter(email__in=[e for e, _ in logins]).values_list('email', flat=True))
        for email, kind in logins:
            self.stdout.write(f'  {"exists" if email in existing else "create"}  {email} ({kind})')
        for (level, term, session), courses, state in (
            (PREVIOUS, PREVIOUS_COURSES, 'finished'), (CURRENT, CURRENT_COURSES, 'active'),
        ):
            found = find_semester(level, term, session) is not None
            codes = ', '.join(c[0] for c in courses)
            label = Semester(level=level, semester=term, session=session).label
            self.stdout.write(f'  {"exists" if found else "create"}  semester {label} ({state}): {codes}')
        self.stdout.write(
            f'  attendance: {", ".join(f"{c[0]} {c[4]}" for c in CURRENT_COURSES + PREVIOUS_COURSES)} class dates '
            f'(only for courses without attendance; counted back from {today.isoformat()})'
        )

    # ── Writing ───────────────────────────────────────────────────────────────

    def _seed(self, today):
        admin = self._user(f'admin@{DOMAIN}', User.Role.ADMIN, 'Demo', 'Admin', is_staff=True, is_superuser=True)
        AdminProfile.objects.get_or_create(user=admin)
        teachers = [self._teacher(*row) for row in TEACHERS]
        self._teacher(*PENDING_TEACHER, verified=False)

        regular = [self._student(sid, first, last) for sid, first, last, _ in STUDENTS]
        deleted = self._student(*DELETED_STUDENT[:3])
        late = self._student(*LATE_JOINER[:3])
        sid, first, last, email = PENDING_STUDENT
        self._student(sid, first, last, email=email, verified=False)
        rates = {sid: rate for sid, _, _, rate in STUDENTS + [LATE_JOINER, DELETED_STUDENT]}

        start = today - timedelta(weeks=SEMESTER_WEEKS)
        previous_start, previous_end = start - timedelta(weeks=20), start - timedelta(days=8)
        previous = self._semester(PREVIOUS, previous_start, previous_end)
        current = self._semester(CURRENT, start, None)

        # Promotion: everyone left Level 2 Term II when Level 3 Term I started; the late joiner
        # came later (counted from the day they joined).
        previous_room, current_room = main_classroom(previous), main_classroom(current)
        for profile in regular + [deleted]:
            StudentClassroom.objects.get_or_create(student=profile, classroom=previous_room,
                                                   defaults={'left_at': start})
            StudentClassroom.objects.get_or_create(student=profile, classroom=current_room)
        StudentClassroom.objects.get_or_create(
            student=late, classroom=current_room, defaults={'joined_at': today - timedelta(days=LATE_JOIN_DAYS)},
        )
        if previous.is_active:
            previous.is_active = False
            previous.save(update_fields=['is_active'])

        for semester, courses, last_day, first_day in (
            (previous, PREVIOUS_COURSES, previous_end, previous_start),
            (current, CURRENT_COURSES, today - timedelta(days=1), start),
        ):
            for code, title, teacher_index, weekdays, count, class_time in courses:
                ci = self._course_info(code, title, teachers[teacher_index], semester)
                if AttendanceLog.objects.filter(course_info=ci).exists():
                    continue
                days = past_dates(weekdays, count, last_day, first_day)
                self._attendance(ci, days, class_time, rates)

        if not deleted.user.deleted:
            deactivate_user(deleted.user)

    def _user(self, email, role, first, last, verified=True, **extra):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={'username': email, 'role': role, 'first_name': first, 'last_name': last,
                      'faculty': FACULTY, 'department': DEPARTMENT, 'is_verified': verified, **extra},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user

    def _teacher(self, email, first, last, employee_id, verified=True):
        user = self._user(email, User.Role.TEACHER, first, last, verified=verified)
        profile, _ = TeacherProfile.objects.get_or_create(user=user, defaults={'employee_id': employee_id})
        return profile

    def _student(self, student_id, first, last, email=None, verified=True):
        user = self._user(email or student_email(student_id), User.Role.STUDENT, first, last, verified=verified)
        profile, _ = StudentProfile.objects.get_or_create(
            user=user, defaults={'student_id': student_id, 'current_level': CURRENT[0],
                                 'current_semester': CURRENT[1]},
        )
        return profile

    def _semester(self, key, start_date, end_date):
        level, term, session = key
        semester = find_semester(level, term, session)
        if semester is None:
            return create_semester(level, term, session, start_date=start_date, end_date=end_date)
        if not semester.session:
            semester.session = session
            semester.start_date = semester.start_date or start_date
            semester.end_date = semester.end_date or end_date
            semester.save(update_fields=['session', 'start_date', 'end_date'])
        return semester

    def _course_info(self, code, title, teacher, semester):
        course, _ = Course.objects.get_or_create(
            code=code, defaults={'title': title, 'credits': Course.Credits.CREDIT_3_00},
        )
        ci = CourseInfo.objects.filter(course=course, semester=semester, deleted=False).first()
        if ci is None:
            ci = CourseInfo.objects.create(
                course=course, teacher=teacher, semester=semester, classroom=main_classroom(semester),
            )
        return ci

    # ── Attendance ────────────────────────────────────────────────────────────

    def _attendance(self, ci, days, class_time, rates):
        """Class dates taken in turn by every CLASS_KINDS method; two corrections in CSE301/CSE303."""
        teacher_user = ci.teacher.user if ci.teacher_id else None
        for index, day in enumerate(days):
            rng = random.Random(f'{ci.course.code}:{index}')
            students = [m.student for m in enrolled_memberships(ci, day).select_related('student')
                        .order_by('student__student_id')]
            present = [s for s in students if rng.random() < rates.get(s.student_id, 0.85)]
            starts = timezone.make_aware(datetime.combine(day, class_time))
            kind = CLASS_KINDS[index % len(CLASS_KINDS)]

            if kind in ('QR', 'ONLINE', 'QR+FACE'):
                online = kind == 'ONLINE'
                # Online, most students type the code; in class most scan the QR
                methods = {s.student_id: ('CODE' if rng.random() < (0.7 if online else 0.2) else 'QR')
                           for s in present}
                self._live_session(ci, day, starts, present, methods, rng, online=online)
            if kind == 'FACE':
                self._face_session(ci, day, starts, present)
            if kind == 'QR+FACE':
                # A face photo later the same day finds two who missed the QR
                late_comers = [s for s in students if s not in present][:2]
                self._face_session(ci, day, starts + timedelta(minutes=45), present + late_comers)
            if kind == 'ROLL_CALL':
                roll_call(ci, day, [s.id for s in present], teacher_user)

            # A teacher correction: someone marked absent was in class after all
            if index == 1 and ci.course.code in ('CSE301', 'CSE303'):
                absent = [s for s in students if s not in present]
                if absent:
                    correct_attendance(ci, absent[0], day, AttendanceLog.Status.PRESENT, teacher_user)

    def _live_session(self, ci, day, starts, present, methods, rng, online=False):
        session = AttendanceSession.objects.create(
            course_info=ci, date=day, mode=AttendanceSession.Mode.QR_ONLINE,
            delivery=AttendanceSession.Delivery.ONLINE if online else AttendanceSession.Delivery.IN_CLASS,
            is_active=False, duration_seconds=300, qr_token=secrets.token_urlsafe(24),
        )
        AttendanceSession.objects.filter(pk=session.pk).update(
            started_at=starts, ended_at=starts + timedelta(minutes=5),
        )
        submissions = {
            str(s.student_id): {
                'method': methods[s.student_id],
                'time': (starts + timedelta(seconds=rng.randint(15, 280))).timestamp(),
            }
            for s in present
        }
        commit_session_to_db(session, submissions)

    def _face_session(self, ci, day, starts, present):
        session = save_face_attendance(ci, [s.id for s in present], day)
        AttendanceSession.objects.filter(pk=session.pk).update(started_at=starts, ended_at=starts)
        AttendanceLog.objects.filter(session=session).update(time=timezone.localtime(starts).time())
