"""POST /api/admin/students/import/ — .csv/.xlsx, dry run unless apply is "true"."""
import io

import openpyxl
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.attendance.models import AttendanceLog
from apps.attendance.tests.helpers import client_for, make_admin, make_student, make_teacher
from apps.users.models import StudentProfile, User
from apps.users.services import TEMP_PASSWORD_PBKDF2_ITERATIONS, temporary_password, temporary_password_hash

URL = '/api/admin/students/import/'

CSV = (
    'Email,Name,Student ID,Level,Term\n'
    'new.one@example.com,Ayesha Siddika Rahman,2302101,Third,I\n'
    'NEW.TWO@example.com,Tanvir Hasan,2302102,3,ii\n'
    ',,,,\n'
    'someone@example.com,Already There,2302001,Third,I\n'
    'existing@example.com,Same Email,2302103,Third,I\n'
    'teacher@example.com,Teacher Email,2302104,Third,I\n'
    'bad@example.com,Bad Level,2302105,Fifth,I\n'
    'dup@example.com,Same Id,2302101,Third,I\n'
    'not-an-email,,abc,,\n'
)


def upload(name, content):
    return SimpleUploadedFile(name, content.encode() if isinstance(content, str) else content)


class ImportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = client_for(make_admin('admin@example.com'))
        make_student('old@example.com', 2302001)
        make_student('existing@example.com', 2302999)
        make_teacher('teacher@example.com')

    def post(self, file, **fields):
        return self.client.post(URL, {'file': file, **fields}, format='multipart')

    def test_dry_run_checks_every_row_and_writes_nothing(self):
        before = User.objects.count()
        res = self.post(upload('students.csv', CSV))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['applied'])
        self.assertNotIn('created', res.data)
        self.assertEqual(res.data['summary'], {'create': 2, 'exists': 2, 'error': 4})
        rows = {r['row']: r for r in res.data['rows']}
        self.assertNotIn(4, rows)  # the empty row is skipped
        self.assertEqual(rows[2], {
            'row': 2, 'student_id': 2302101, 'name': 'Ayesha Siddika Rahman', 'email': 'new.one@example.com',
            'level': 'Third', 'term': 'I', 'status': 'create', 'errors': [],
        })
        self.assertEqual((rows[3]['email'], rows[3]['level'], rows[3]['term']), ('new.two@example.com', 'Third', 'II'))
        self.assertEqual(rows[5]['status'], 'exists')  # same student id
        self.assertEqual(rows[6]['status'], 'exists')  # same email
        self.assertEqual(rows[7]['errors'], ['This email belongs to a teacher or admin account.'])
        self.assertEqual(rows[8]['errors'], ['Level must be First, Second, Third or Fourth.'])
        self.assertEqual(rows[9]['errors'], ['Same student ID as row 2.'])
        self.assertEqual(rows[10]['errors'], [
            'Student ID must be a whole number.', 'Name is missing.', 'Email is not valid.', 'Level is missing.',
            'Term is missing.',
        ])
        self.assertEqual(User.objects.count(), before)

    def test_apply_creates_approved_accounts_with_temporary_passwords(self):
        res = self.post(upload('students.csv', CSV), apply='true')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['applied'])
        created = res.data['created']
        self.assertEqual([c['student_id'] for c in created], [2302101, 2302102])
        for item in created:
            self.assertEqual(len(item['temporary_password']), 10)
            user = User.objects.get(email=item['email'])
            self.assertTrue(user.is_verified)
            self.assertEqual(user.role, 'STUDENT')
            login = APIClient().post('/api/auth/login/', {'email': item['email'],
                                                         'password': item['temporary_password']}, format='json')
            self.assertEqual(login.status_code, 200)
        user = User.objects.get(email='new.one@example.com')
        self.assertEqual((user.first_name, user.last_name), ('Ayesha Siddika', 'Rahman'))
        self.assertEqual(StudentProfile.objects.get(student_id=2302102).current_semester, 'II')
        # Existing accounts are untouched
        self.assertEqual(StudentProfile.objects.get(student_id=2302999).user.email, 'existing@example.com')

        # Running it again finds them
        again = self.post(upload('students.csv', CSV), apply='true')
        self.assertEqual(again.data['summary']['create'], 0)
        self.assertEqual(again.data['created'], [])

    def test_anything_but_true_is_a_dry_run(self):
        for value in ('false', '1', 'yes', ''):
            res = self.post(upload('students.csv', CSV), apply=value)
            self.assertFalse(res.data['applied'])
        self.assertFalse(User.objects.filter(email='new.one@example.com').exists())

    def test_semester_id_adds_the_new_students(self):
        semester = Semester.objects.create(level='Third', semester='I')
        res = self.post(upload('students.csv', CSV), apply='true', semester_id=str(semester.id))
        self.assertEqual(res.status_code, 200)
        classroom = Classroom.objects.get(semester=semester)
        self.assertEqual(classroom.name, 'Main')  # made because the semester had none
        members = set(StudentClassroom.objects.filter(classroom=classroom).values_list('student__student_id', flat=True))
        self.assertEqual(members, {2302101, 2302102})

    def test_semester_id_uses_the_existing_class_group(self):
        semester = Semester.objects.create(level='Third', semester='I')
        main = Classroom.objects.create(name='Main', semester=semester)
        self.post(upload('students.csv', CSV), apply='true', semester_id=str(semester.id))
        self.assertEqual(Classroom.objects.filter(semester=semester).count(), 1)
        self.assertEqual(StudentClassroom.objects.filter(classroom=main).count(), 2)

    def test_semester_id_joins_like_the_roster(self):
        """Before the semester's first class: from the start; after it: from today (late joiners)."""
        semester = Semester.objects.create(level='Third', semester='I')
        self.post(upload('students.csv', CSV), apply='true', semester_id=str(semester.id))
        self.assertEqual(set(StudentClassroom.objects.values_list('joined_at', 'left_at')), {(None, None)})

        main = Classroom.objects.get(semester=semester)
        ci = CourseInfo.objects.create(course=Course.objects.create(code='CSE301', title='SE'),
                                       semester=semester, classroom=main)
        AttendanceLog.objects.create(course_info=ci, student=StudentProfile.objects.get(student_id=2302101),
                                     date=timezone.localdate(), status='PRESENT')
        self.post(upload('more.csv', 'email,name,student_id,level,term\nlate@example.com,Late One,2302200,Third,I\n'),
                  apply='true', semester_id=str(semester.id))
        late = StudentClassroom.objects.get(student__student_id=2302200)
        self.assertEqual(late.joined_at, timezone.localdate())

    def test_unknown_semester(self):
        for value in ('not-a-uuid', '6f1c1f2e-1111-4a4a-9b9b-123456789abc'):
            res = self.post(upload('students.csv', CSV), semester_id=value)
            self.assertEqual(res.status_code, 400)
            self.assertIn('semester_id', res.data['errors'])
            self.assertEqual(res.data['message'], 'This semester was not found.')

    def test_xlsx_with_first_and_last_name_columns(self):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['student_id', 'first_name', 'last_name', 'EMAIL', 'level', 'term'])
        sheet.append([2302201, 'Nusrat', 'Jahan', 'nusrat@example.com', 'Fourth', 'II'])
        sheet.append([2302202.0, 'Rafi', None, 'rafi@example.com', '4th', 2])
        buffer = io.BytesIO()
        workbook.save(buffer)

        res = self.post(upload('students.xlsx', buffer.getvalue()), apply='true')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['summary'], {'create': 2, 'exists': 0, 'error': 0})
        self.assertEqual([r['name'] for r in res.data['rows']], ['Nusrat Jahan', 'Rafi'])
        profile = StudentProfile.objects.get(student_id=2302202)
        self.assertEqual((profile.current_level, profile.current_semester), ('Fourth', 'II'))

    def test_semicolon_csv(self):
        content = 'student_id;name;email;level;term\n2302301;Mim Akter;mim@example.com;First;I\n'
        res = self.post(upload('students.csv', content))
        self.assertEqual(res.data['summary']['create'], 1)

    def test_email_longer_than_150_characters_is_a_row_error(self):
        long_email = f'{"m" * 140}@example.com'
        content = f'student_id,name,email,level,term\n2302301,Mim Akter,{long_email},First,I\n'
        res = self.post(upload('students.csv', content), apply='true')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['summary'], {'create': 0, 'exists': 0, 'error': 1})
        self.assertEqual(res.data['rows'][0]['errors'], ['Email is too long.'])
        self.assertFalse(User.objects.filter(email=long_email).exists())

    def test_file_problems(self):
        cases = [
            (upload('students.txt', CSV), 'Upload a .csv or .xlsx file.'),
            (upload('students.csv', 'student_id,name,level\n1,A,First\n'),
             'The file is missing the columns email, term. '
             'The first row must name the columns: student_id, name, email, level, term.'),
            (upload('students.csv', ''), 'The file is empty.'),
            (upload('students.xlsx', b'not a zip'), 'This Excel file could not be read. Save it as .xlsx and try again.'),
        ]
        for file, message in cases:
            res = self.post(file)
            self.assertEqual(res.status_code, 400, message)
            self.assertEqual(res.data['code'], 'invalid_file')
            self.assertEqual(res.data['message'], message)

    def test_no_file(self):
        res = self.client.post(URL, {'apply': 'true'}, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['message'], 'Choose a .csv or .xlsx file.')

    def test_admins_only(self):
        teacher = make_teacher('t2@example.com')
        res = client_for(teacher.user).post(URL, {'file': upload('students.csv', CSV)}, format='multipart')
        self.assertEqual(res.status_code, 403)


class TemporaryPasswordTests(TestCase):
    def test_shape(self):
        for _ in range(50):
            password = temporary_password()
            self.assertEqual(len(password), 10)
            self.assertTrue(any(c.isdigit() for c in password) and any(c.isalpha() for c in password))
            self.assertFalse(set(password) & set('0O1lI'))

    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_quick_hash_is_upgraded_at_first_sign_in(self):
        password = temporary_password()
        user = User.objects.create(email='t@example.com', username='t@example.com', role='STUDENT', is_verified=True,
                                   password=temporary_password_hash(password))
        self.assertTrue(user.password.startswith(f'pbkdf2_sha256${TEMP_PASSWORD_PBKDF2_ITERATIONS}$'))
        self.assertTrue(check_password(password, user.password))
        self.assertTrue(user.check_password(password))
        user.refresh_from_db()
        self.assertFalse(user.password.startswith(f'pbkdf2_sha256${TEMP_PASSWORD_PBKDF2_ITERATIONS}$'))
