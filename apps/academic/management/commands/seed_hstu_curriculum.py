"""
Seeds all courses from HSTU CSE 2017 Syllabus (154.75 credits total, 107 courses),
real HSTU CSE teachers, and realistic dummy students into the database.

Usage:
    python manage.py seed_hstu_curriculum --settings=config.settings.test           # dry run
    python manage.py seed_hstu_curriculum --settings=config.settings.test --apply   # write
    python manage.py seed_hstu_curriculum --apply --allow-remote                    # write to configured db
"""
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.academic.services import main_classroom
from apps.users.constants import DEPARTMENT, FACULTY
from apps.users.models import StudentProfile, TeacherProfile, User

DEFAULT_PASSWORD = 'HstuCse@2026'

# Real faculty members of HSTU Computer Science & Engineering department
HSTU_CSE_TEACHERS = [
    # (email, first_name, last_name, designation, emp_id)
    ('mamun@hstu.ac.bd', 'Dr. Md. Abdulla Al', 'Mamun', 'Professor & Chairman', 'EMP-CSE-001'),
    ('nitu@hstu.ac.bd', 'Adiba Mahjabin', 'Nitu', 'Professor', 'EMP-CSE-002'),
    ('delowar@hstu.ac.bd', 'Dr. Md. Delowar', 'Hossain', 'Professor', 'EMP-CSE-003'),
    ('ashis@hstu.ac.bd', 'Dr. Ashis Kumar', 'Mandal', 'Professor', 'EMP-CSE-004'),
    ('arshad@hstu.ac.bd', 'Dr. Md. Arshad', 'Ali', 'Professor', 'EMP-CSE-005'),
    ('rabbi@hstu.ac.bd', 'Md. Fazle', 'Rabbi', 'Associate Professor', 'EMP-CSE-006'),
    ('masud@hstu.ac.bd', 'Dr. Masud Ibn', 'Afjal', 'Associate Professor', 'EMP-CSE-007'),
    ('hasi@hstu.ac.bd', 'Hasi', 'Saha', 'Associate Professor', 'EMP-CSE-008'),
    ('palash@hstu.ac.bd', 'Dr. Md. Palash', 'Uddin', 'Associate Professor', 'EMP-CSE-009'),
    ('rashedul@hstu.ac.bd', 'Md. Rashedul', 'Islam', 'Associate Professor', 'EMP-CSE-010'),
    ('nahid@hstu.ac.bd', 'Md. Nahid', 'Sultan', 'Associate Professor', 'EMP-CSE-011'),
    ('sohrawordi@hstu.ac.bd', 'Md.', 'Sohrawordi', 'Associate Professor', 'EMP-CSE-012'),
    ('nadim@hstu.ac.bd', 'Dr. Md.', 'Nadim', 'Associate Professor', 'EMP-CSE-013'),
    ('habib@hstu.ac.bd', 'Ahsan', 'Habib', 'Assistant Professor', 'EMP-CSE-014'),
    ('emran@hstu.ac.bd', 'Emran', 'Ali', 'Assistant Professor', 'EMP-CSE-015'),
    ('ehsan@hstu.ac.bd', 'Dr. U. A. Md. Ehsan', 'Ali', 'Assistant Professor', 'EMP-CSE-016'),
    ('shajalal@hstu.ac.bd', 'Md.', 'Shajalal', 'Assistant Professor', 'EMP-CSE-017'),
    ('marjan@hstu.ac.bd', 'Md. Abu', 'Marjan', 'Assistant Professor', 'EMP-CSE-018'),
    ('sumya@hstu.ac.bd', 'Sumya', 'Akter', 'Assistant Professor', 'EMP-CSE-019'),
    ('pankaj@hstu.ac.bd', 'Pankaj', 'Bhowmik', 'Assistant Professor', 'EMP-CSE-020'),
    ('tazel@hstu.ac.bd', 'Md. Tazel', 'Hossan', 'Lecturer', 'EMP-CSE-021'),
    ('ashraful@hstu.ac.bd', 'Md. Ashraful', 'Islam', 'Lecturer', 'EMP-CSE-022'),
    ('fahmina@hstu.ac.bd', 'Fahmina', 'Akhtar', 'Lecturer', 'EMP-CSE-023'),
    ('hanif@hstu.ac.bd', 'Md. Abu', 'Hanif', 'Lecturer', 'EMP-CSE-024'),
]

# HSTU CSE 2017 Syllabus Courses
# (code, title, credits_enum, level, term, department_override)
CURRICULUM_COURSES = [
    # ── Level 1 Semester I (19.00 Credits) ──────────────────────────────────
    ('CSE 101', 'Fundamentals of Computer and Computing', Course.Credits.CREDIT_2_00, 'First', 'I', None),
    ('CSE 102', 'Fundamentals of Computer and Computing Sessional', Course.Credits.CREDIT_0_75, 'First', 'I', None),
    ('CSE 103', 'Discrete Mathematics', Course.Credits.CREDIT_3_00, 'First', 'I', None),
    ('MAT 101', 'Mathematics 1 (Calculus and Co-ordinate Geometry)', Course.Credits.CREDIT_3_00, 'First', 'I', 'MATHEMATICS'),
    ('PHY 103', 'Physics (Electricity, Magnetism, Optics, Waves and Oscillations)', Course.Credits.CREDIT_3_00, 'First', 'I', 'PHYSICS'),
    ('PHY 104', 'Physics (Electricity, Magnetism, Optics, Waves, and Oscillations) Sessional', Course.Credits.CREDIT_1_50, 'First', 'I', 'PHYSICS'),
    ('AIE 105', 'Basic Mechanical Engineering', Course.Credits.CREDIT_3_00, 'First', 'I', 'AGRICULTURAL_AND_INDUSTRIAL_ENGINEERING'),
    ('ENG 101', 'Communicative English', Course.Credits.CREDIT_2_00, 'First', 'I', 'ENGLISH'),
    ('ENG 102', 'Communicative English Sessional', Course.Credits.CREDIT_0_75, 'First', 'I', 'ENGLISH'),

    # ── Level 1 Semester II (19.25 Credits) ─────────────────────────────────
    ('CSE 151', 'Structured Programming Language', Course.Credits.CREDIT_3_00, 'First', 'II', None),
    ('CSE 152', 'Structured Programming Language Sessional', Course.Credits.CREDIT_1_50, 'First', 'II', None),
    ('CSE 153', 'Digital Logic Design', Course.Credits.CREDIT_3_00, 'First', 'II', None),
    ('CSE 154', 'Digital Logic Design Sessional', Course.Credits.CREDIT_1_50, 'First', 'II', None),
    ('EEE 155', 'Introduction to Electrical Engineering', Course.Credits.CREDIT_3_00, 'First', 'II', 'ELECTRICAL_AND_ELECTRONIC_ENGINEERING'),
    ('EEE 156', 'Introduction to Electrical Engineering Sessional', Course.Credits.CREDIT_0_75, 'First', 'II', 'ELECTRICAL_AND_ELECTRONIC_ENGINEERING'),
    ('AIE 106', 'Engineering Drawing and Auto CAD Sessional', Course.Credits.CREDIT_1_50, 'First', 'II', 'AGRICULTURAL_AND_INDUSTRIAL_ENGINEERING'),
    ('MAT 105', 'Mathematics II (Matrix, Ordinary and Partial Differential Equations, and Series Solutions)', Course.Credits.CREDIT_3_00, 'First', 'II', 'MATHEMATICS'),
    ('SOC 103', 'Society and Technology', Course.Credits.CREDIT_2_00, 'First', 'II', 'SOCIOLOGY'),

    # ── Level 2 Semester I (21.50 Credits) ──────────────────────────────────
    ('CSE 201', 'Object Oriented Programming', Course.Credits.CREDIT_3_00, 'Second', 'I', None),
    ('CSE 202', 'Object Oriented Programming (C++) Sessional', Course.Credits.CREDIT_1_50, 'Second', 'I', None),
    ('CSE 203', 'Data Structures', Course.Credits.CREDIT_3_00, 'Second', 'I', None),
    ('CSE 204', 'Data Structures Sessional', Course.Credits.CREDIT_1_50, 'Second', 'I', None),
    ('CSE 205', 'Numerical Methods', Course.Credits.CREDIT_2_00, 'Second', 'I', None),
    ('CSE 206', 'Numerical Methods Sessional', Course.Credits.CREDIT_0_75, 'Second', 'I', None),
    ('EEE 209', 'Electronic Devices and Circuits', Course.Credits.CREDIT_3_00, 'Second', 'I', 'ELECTRICAL_AND_ELECTRONIC_ENGINEERING'),
    ('EEE 210', 'Electronic Devices and Circuits Sessional', Course.Credits.CREDIT_0_75, 'Second', 'I', 'ELECTRICAL_AND_ELECTRONIC_ENGINEERING'),
    ('MAT 201', 'Mathematics III (Vector, Complex Variable, Fourier Analysis and Laplace Transformation)', Course.Credits.CREDIT_3_00, 'Second', 'I', 'MATHEMATICS'),
    ('STT 227', 'Statistics (Introduction to Statistics and Probability)', Course.Credits.CREDIT_3_00, 'Second', 'I', 'STATISTICS'),

    # ── Level 2 Semester II (20.00 Credits) ─────────────────────────────────
    ('CSE 254', 'Object Oriented Programming (Java) Sessional', Course.Credits.CREDIT_1_50, 'Second', 'II', None),
    ('CSE 255', 'Algorithms Analysis and Design', Course.Credits.CREDIT_3_00, 'Second', 'II', None),
    ('CSE 256', 'Algorithms Analysis and Design Sessional', Course.Credits.CREDIT_1_50, 'Second', 'II', None),
    ('CSE 257', 'Theory of Computation and Concrete Mathematics', Course.Credits.CREDIT_3_00, 'Second', 'II', None),
    ('CSE 258', 'Theory of Computation and Concrete Mathematics Sessional', Course.Credits.CREDIT_0_75, 'Second', 'II', None),
    ('CSE 259', 'Computer Architecture and Organization', Course.Credits.CREDIT_3_00, 'Second', 'II', None),
    ('ECE 259', 'Digital Electronics and Pulse Techniques', Course.Credits.CREDIT_3_00, 'Second', 'II', 'ELECTRONICS_AND_COMMUNICATION_ENGINEERING'),
    ('ECE 260', 'Digital Electronics and Pulse Techniques Sessional', Course.Credits.CREDIT_0_75, 'Second', 'II', 'ELECTRONICS_AND_COMMUNICATION_ENGINEERING'),
    ('ACT 205', 'Financial and Managerial Accounting', Course.Credits.CREDIT_2_00, 'Second', 'II', 'ACCOUNTING'),
    ('CSE 252', 'Application Development Sessional', Course.Credits.CREDIT_1_50, 'Second', 'II', None),

    # ── Level 3 Semester I (18.50 Credits) ──────────────────────────────────
    ('CSE 303', 'Database', Course.Credits.CREDIT_3_00, 'Third', 'I', None),
    ('CSE 304', 'Database Sessional', Course.Credits.CREDIT_1_50, 'Third', 'I', None),
    ('CSE 305', 'Software Engineering', Course.Credits.CREDIT_3_00, 'Third', 'I', None),
    ('CSE 307', 'Microprocessor and Interfacing', Course.Credits.CREDIT_3_00, 'Third', 'I', None),
    ('CSE 308', 'Microprocessor and Interfacing Sessional', Course.Credits.CREDIT_1_50, 'Third', 'I', None),
    ('ECE 311', 'Data Communication', Course.Credits.CREDIT_3_00, 'Third', 'I', 'ELECTRONICS_AND_COMMUNICATION_ENGINEERING'),
    ('ECN 305', 'Economics', Course.Credits.CREDIT_2_00, 'Third', 'I', 'ECONOMICS'),
    ('CSE 302', 'Software Development Sessional', Course.Credits.CREDIT_1_50, 'Third', 'I', None),

    # ── Level 3 Semester II (18.50 Credits) ─────────────────────────────────
    ('CSE 353', 'Operating System', Course.Credits.CREDIT_3_00, 'Third', 'II', None),
    ('CSE 354', 'Operating System Sessional', Course.Credits.CREDIT_0_75, 'Third', 'II', None),
    ('CSE 355', 'Web Engineering', Course.Credits.CREDIT_2_00, 'Third', 'II', None),
    ('CSE 356', 'Web Engineering Sessional', Course.Credits.CREDIT_0_75, 'Third', 'II', None),
    ('CSE 357', 'Computer Networks', Course.Credits.CREDIT_3_00, 'Third', 'II', None),
    ('CSE 358', 'Computer Networks Sessional', Course.Credits.CREDIT_0_75, 'Third', 'II', None),
    ('CSE 359', 'Compiler Design', Course.Credits.CREDIT_3_00, 'Third', 'II', None),
    ('CSE 360', 'Compiler Design Sessional', Course.Credits.CREDIT_0_75, 'Third', 'II', None),
    ('CSE 361', 'Mathematical Analysis for Computer Science', Course.Credits.CREDIT_3_00, 'Third', 'II', None),
    ('CSE 352', 'Web and Mobile Application Development Sessional', Course.Credits.CREDIT_1_50, 'Third', 'II', None),

    # ── Level 4 Semester I (18.75 Credits with Options) ────────────────────
    ('CSE 403', 'Artificial Intelligence', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 404', 'Artificial Intelligence Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 405', 'Computer Graphics and Image Processing', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 406', 'Computer Graphics and Image Processing Sessional', Course.Credits.CREDIT_1_50, 'Fourth', 'I', None),
    ('CSE 408', 'Technical Writing and Presentation Skill Development Sessional', Course.Credits.CREDIT_1_50, 'Fourth', 'I', None),
    ('CSE 402', 'Project and Thesis Sessional', Course.Credits.CREDIT_1_50, 'Fourth', 'I', None),

    # Option I (Level 4 Semester I)
    ('CSE 409', 'Advanced Database Management System', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 410', 'Advanced Database Management System Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 411', 'Advanced Algorithm Design', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 412', 'Advanced Algorithm Design Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 413', 'Management Information System', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 414', 'Management Information System Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 415', 'Mobile and Wireless Communication', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 416', 'Mobile and Wireless Communication Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 417', 'Communication Engineering', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 418', 'Communication Engineering Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),

    # Option II (Level 4 Semester I)
    ('CSE 419', 'System Analysis and Design', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 420', 'System Analysis and Design Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 421', 'Software Testing and Quality Assurance', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 422', 'Software Testing and Quality Assurance Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 423', 'Graph Theory', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 424', 'Graph Theory Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 425', 'Cryptography and Network Security', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 426', 'Cryptography and Network Security Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),
    ('CSE 427', 'Simulation and Modelling', Course.Credits.CREDIT_3_00, 'Fourth', 'I', None),
    ('CSE 428', 'Simulation and Modelling Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'I', None),

    # ── Level 4 Semester II (19.25 Credits with Options) ───────────────────
    ('CSE 453', 'Multimedia System and Animation Techniques', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 454', 'Multimedia System and Animation Techniques Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 455', 'Computer Ethics and Cyber Law', Course.Credits.CREDIT_2_00, 'Fourth', 'II', None),
    ('MGT 405', 'Industrial Management', Course.Credits.CREDIT_3_00, 'Fourth', 'II', 'MANAGEMENT'),
    ('CSE 452', 'Project and Thesis Sessional', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),

    # Option III (Level 4 Semester II)
    ('CSE 459', 'Data Mining and Warehousing', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 460', 'Data Mining and Warehousing Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 461', 'Cloud Computing', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 462', 'Cloud Computing Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 463', 'VLSI Design', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 464', 'VLSI Design Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 465', 'Digital System Design', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 466', 'Digital System Design Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 467', 'Parallel and Distributed System', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 468', 'Parallel and Distributed System Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),

    # Option IV (Level 4 Semester II)
    ('CSE 469', 'Machine Learning and Pattern Recognition', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 470', 'Machine Learning and Pattern Recognition Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 471', 'Natural Language Processing', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 472', 'Natural Language Processing Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 473', 'Human and Computer Interaction', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 474', 'Human and Computer Interaction Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 475', 'Robotics', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 476', 'Robotics Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
    ('CSE 477', 'Bioinformatics', Course.Credits.CREDIT_3_00, 'Fourth', 'II', None),
    ('CSE 478', 'Bioinformatics Sessional', Course.Credits.CREDIT_0_75, 'Fourth', 'II', None),
]

# Student names for dummy batch generator
FIRST_NAMES = [
    'Afsana', 'Arif', 'Ashiqur', 'Asif', 'Fahim', 'Farhan', 'Farzana', 'Hasibul',
    'Ishrat', 'Mahmudul', 'Mehedi', 'Naimur', 'Nasrin', 'Nazmul', 'Nusrat',
    'Rafiqul', 'Rakibul', 'Rifat', 'Sabbir', 'Sadia', 'Shahriar', 'Sharmin',
    'Sumaiya', 'Tahmid', 'Tanvir', 'Tasnim', 'Touhidul', 'Umme', 'Zahid', 'Zubair'
]

LAST_NAMES = [
    'Ahmed', 'Akter', 'Ali', 'Chowdhury', 'Hasan', 'Hossain', 'Islam', 'Jahan',
    'Karim', 'Khan', 'Miah', 'Parvin', 'Rahman', 'Sarker', 'Siddique', 'Sultana'
]

# Semester setups (Level, Term, Session, Batch Code, Year Prefix)

# Pending approval accounts (teachers and students awaiting admin verification)
PENDING_TEACHERS = [
    # (email, first_name, last_name, employee_id)
    ('pending.teacher1@hstu.ac.bd', 'Kazi', 'Tanvir Ahmed', 'EMP-CSE-025'),
    ('pending.teacher2@hstu.ac.bd', 'Mahmuda', 'Khatun', 'EMP-CSE-026'),
    ('pending.teacher3@hstu.ac.bd', 'Shubhashis', 'Roy', 'EMP-CSE-027'),
]

PENDING_STUDENTS = [
    # (email, student_id, first_name, last_name, current_level, current_semester)
    ('pending.student2402016@hstu.ac.bd', 2402016, 'Nadia', 'Sultana', 'First', 'I'),
    ('pending.student2402017@hstu.ac.bd', 2402017, 'Samiul', 'Haque', 'First', 'I'),
    ('pending.student2302016@hstu.ac.bd', 2302016, 'Tanjila', 'Akter', 'Second', 'I'),
    ('pending.student2202016@hstu.ac.bd', 2202016, 'Mahfuzur', 'Rahman', 'Third', 'I'),
    ('pending.student2102016@hstu.ac.bd', 2102016, 'Shoriful', 'Islam', 'Fourth', 'I'),
]

SEMESTER_CONFIGS = [
    ('First', 'I', '2024-25', 'Batch 24', 24),
    ('Second', 'I', '2023-24', 'Batch 23', 23),
    ('Third', 'I', '2022-23', 'Batch 22', 22),
    ('Fourth', 'I', '2021-22', 'Batch 21', 21),
]


class Command(BaseCommand):
    help = 'Seed HSTU CSE 2017 syllabus courses, HSTU teachers, and dummy students (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually commit changes to the database.')
        parser.add_argument('--allow-remote', action='store_true', help='Acknowledge writing to non-SQLite database.')
        parser.add_argument('--students-per-batch', type=int, default=15, help='Number of dummy students per batch (default 15).')
        parser.add_argument('--password', type=str, default=DEFAULT_PASSWORD, help='Default password for seeded accounts.')

    def handle(self, *args, **options):
        is_apply = options['apply']
        allow_remote = options['allow_remote']
        students_per_batch = options['students_per_batch']
        password = options['password']

        db_vendor = connection.vendor
        db_name = connection.settings_dict.get('NAME')
        self.stdout.write(f'Target Database: {db_vendor} ({db_name})')

        if is_apply and db_vendor != 'sqlite' and not allow_remote:
            raise CommandError(
                'Safety protection: connecting to non-SQLite database without --allow-remote. '
                'Please supply --allow-remote if you intend to seed this database.'
            )

        self.stdout.write(self.style.MIGRATE_HEADING('HSTU CSE Curriculum Seeding Plan:'))
        self.stdout.write(f'  • Curriculum courses to seed: {len(CURRICULUM_COURSES)}')
        self.stdout.write(f'  • HSTU CSE faculty members:   {len(HSTU_CSE_TEACHERS)}')
        self.stdout.write(f'  • Semesters configured:       {len(SEMESTER_CONFIGS)}')
        self.stdout.write(f'  • Dummy students per batch:   {students_per_batch} (Total {students_per_batch * len(SEMESTER_CONFIGS)})')
        self.stdout.write(f'  • Pending approval users:     {len(PENDING_TEACHERS)} teachers, {len(PENDING_STUDENTS)} students')

        if not is_apply:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] No changes were written. Run with --apply to commit.'))
            return

        hashed_password = make_password(password)
        with transaction.atomic():
            courses_created, courses_updated = self._seed_courses()
            teachers_created = self._seed_teachers(hashed_password)
            semesters_ready, classrooms_ready, course_infos_created, students_enrolled = self._seed_academic_structure(
                students_per_batch, hashed_password
            )
            pending_teachers, pending_students = self._seed_pending_users(hashed_password)

        self.stdout.write(self.style.SUCCESS('\n✓ Successfully seeded HSTU CSE 2017 Curriculum Data:'))
        self.stdout.write(f'  - Courses: {courses_created} created, {courses_updated} existing verified')
        self.stdout.write(f'  - Faculty Teachers: {teachers_created} active teacher accounts')
        self.stdout.write(f'  - Semesters: {semesters_ready} with classrooms {classrooms_ready}')
        self.stdout.write(f'  - Course Assignments: {course_infos_created} CourseInfo rows linked to teachers')
        self.stdout.write(f'  - Students Enrolled: {students_enrolled} across 4 batches')
        self.stdout.write(f'  - Pending Approvals: {pending_teachers} teachers, {pending_students} students (is_verified=False)')
        self.stdout.write(f'  - Default password for accounts: {password}')

    def _seed_courses(self):
        created_count = 0
        updated_count = 0
        for code, title, credits_val, level, term, dept_override in CURRICULUM_COURSES:
            dept = dept_override or DEPARTMENT
            course, created = Course.objects.update_or_create(
                code=code,
                defaults={
                    'title': title,
                    'credits': credits_val,
                    'faculty': FACULTY,
                    'department': dept,
                    'deleted': False,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1
        return created_count, updated_count

    def _seed_teachers(self, hashed_password):
        count = 0
        for email, first, last, designation, emp_id in HSTU_CSE_TEACHERS:
            user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.create(
                    email=email,
                    username=email,
                    password=hashed_password,
                    role=User.Role.TEACHER,
                    first_name=first,
                    last_name=last,
                    faculty=FACULTY,
                    department=DEPARTMENT,
                    is_verified=True,
                )
            tp = TeacherProfile.objects.filter(user=user).first()
            if not tp:
                TeacherProfile.objects.get_or_create(
                    user=user,
                    defaults={'employee_id': emp_id}
                )
            count += 1
        return count

    def _seed_academic_structure(self, students_per_batch, hashed_password):
        today = timezone.localdate()
        teachers = list(TeacherProfile.objects.filter(user__department=DEPARTMENT, user__deleted=False))

        semesters_count = 0
        classrooms_count = 0
        course_infos_count = 0
        students_count = 0

        for level, term, session, batch_label, year_prefix in SEMESTER_CONFIGS:
            semester, sem_created = Semester.objects.get_or_create(
                level=level,
                semester=term,
                session=session,
                defaults={'is_active': True, 'start_date': today - timedelta(days=90), 'deleted': False}
            )
            semesters_count += 1
            room = main_classroom(semester)
            classrooms_count += 1

            # Find courses corresponding to this semester
            semester_courses = [
                c for c in CURRICULUM_COURSES
                if c[3] == level and c[4] == term
            ]
            for idx, (code, title, credits_val, _, _, _) in enumerate(semester_courses):
                course = Course.objects.get(code=code)
                assigned_teacher = teachers[idx % len(teachers)] if teachers else None
                ci, ci_created = CourseInfo.objects.get_or_create(
                    course=course,
                    semester=semester,
                    classroom=room,
                    defaults={'teacher': assigned_teacher, 'deleted': False}
                )
                if ci_created:
                    course_infos_count += 1
                elif ci.teacher != assigned_teacher and assigned_teacher is not None:
                    ci.teacher = assigned_teacher
                    ci.save(update_fields=['teacher'])

            # Dummy students for this batch
            # HSTU Student ID: YY02XXX (e.g. 2402001 .. 2402015)
            dept_code = '02'
            for roll in range(1, students_per_batch + 1):
                student_id = int(f'{year_prefix}{dept_code}{roll:03d}')
                first = FIRST_NAMES[(year_prefix * 7 + roll) % len(FIRST_NAMES)]
                last = LAST_NAMES[(year_prefix * 5 + roll) % len(LAST_NAMES)]
                email = f'student{student_id}@hstu.ac.bd'

                student_profile = StudentProfile.objects.filter(student_id=student_id).first()
                if not student_profile:
                    s_user = User.objects.filter(email=email).first()
                    if not s_user:
                        s_user = User.objects.create(
                            email=email,
                            username=email,
                            password=hashed_password,
                            role=User.Role.STUDENT,
                            first_name=first,
                            last_name=last,
                            faculty=FACULTY,
                            department=DEPARTMENT,
                            is_verified=True,
                        )
                    student_profile = StudentProfile.objects.filter(user=s_user).first()
                    if not student_profile:
                        student_profile = StudentProfile.objects.create(
                            user=s_user,
                            student_id=student_id,
                            current_level=level,
                            current_semester=term,
                        )

                StudentClassroom.objects.get_or_create(
                    student=student_profile,
                    classroom=room,
                    defaults={'joined_at': today - timedelta(days=90)}
                )
                students_count += 1

        return semesters_count, classrooms_count, course_infos_count, students_count

    def _seed_pending_users(self, hashed_password):
        pending_teachers = 0
        for email, first, last, emp_id in PENDING_TEACHERS:
            user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.create(
                    email=email,
                    username=email,
                    password=hashed_password,
                    role=User.Role.TEACHER,
                    first_name=first,
                    last_name=last,
                    faculty=FACULTY,
                    department=DEPARTMENT,
                    is_verified=False,
                )
            tp = TeacherProfile.objects.filter(user=user).first()
            if not tp:
                TeacherProfile.objects.get_or_create(
                    user=user,
                    defaults={'employee_id': emp_id}
                )
            pending_teachers += 1

        pending_students = 0
        for email, sid, first, last, level, term in PENDING_STUDENTS:
            student_profile = StudentProfile.objects.filter(student_id=sid).first()
            if not student_profile:
                user = User.objects.filter(email=email).first()
                if not user:
                    user = User.objects.create(
                        email=email,
                        username=email,
                        password=hashed_password,
                        role=User.Role.STUDENT,
                        first_name=first,
                        last_name=last,
                        faculty=FACULTY,
                        department=DEPARTMENT,
                        is_verified=False,
                    )
                student_profile = StudentProfile.objects.filter(user=user).first()
                if not student_profile:
                    student_profile = StudentProfile.objects.create(
                        user=user,
                        student_id=sid,
                        current_level=level,
                        current_semester=term,
                    )
            pending_students += 1

        return pending_teachers, pending_students
