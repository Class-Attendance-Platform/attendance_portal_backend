"""
Admin student import from a .csv or .xlsx file (POST /api/admin/students/import/).

The first row names the columns (any order, any capitals): student_id, name (or
first_name + last_name), email, level, term. Each row is checked and marked
"create", "exists" (a student with that student id or email is already there; skipped)
or "error". Nothing is written unless the admin applies; then the "create" rows
become approved accounts with random temporary passwords, shown once.
"""
import csv
import io
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower

from apps.academic.models import Classroom, StudentClassroom
from apps.users.constants import DEPARTMENT, FACULTY, LEVELS, TERMS
from apps.users.models import StudentProfile, User
from apps.users.services import temporary_password, temporary_password_hash

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
MAX_STUDENT_ID = 2147483647

HEADER_ALIASES = {
    'student_id': 'student_id', 'studentid': 'student_id', 'student_no': 'student_id', 'roll': 'student_id',
    'name': 'name', 'full_name': 'name', 'student_name': 'name',
    'first_name': 'first_name', 'last_name': 'last_name',
    'email': 'email', 'e_mail': 'email', 'email_address': 'email',
    'level': 'level', 'current_level': 'level',
    'term': 'term', 'semester': 'term', 'current_semester': 'term',
}
REQUIRED_COLUMNS = ['student_id', 'email', 'level', 'term']

LEVEL_WORDS = {}
for _number, _level in enumerate(LEVELS, start=1):
    _ordinal = {1: '1st', 2: '2nd', 3: '3rd', 4: '4th'}[_number]
    for _word in (_level, str(_number), _ordinal, f'level {_number}', f'level{_number}', f'l{_number}'):
        LEVEL_WORDS[_word.lower()] = _level
TERM_WORDS = {'i': 'I', '1': 'I', 'term i': 'I', 'term 1': 'I', 'ii': 'II', '2': 'II', 'term ii': 'II', 'term 2': 'II'}
assert set(TERM_WORDS.values()) == set(TERMS)


class ImportFileError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ── Reading the file ──────────────────────────────────────────────────────────


def _cell(value) -> str:
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_table(upload):
    """[(spreadsheet row number, [cell texts])] for the non-empty rows, header first."""
    name = (getattr(upload, 'name', '') or '').lower()
    if upload.size > MAX_FILE_BYTES:
        raise ImportFileError('The file is too large (2 MB at most).')
    if name.endswith('.csv'):
        raw_rows = _read_csv(upload.read())
    elif name.endswith('.xlsx'):
        raw_rows = _read_xlsx(upload)
    else:
        raise ImportFileError('Upload a .csv or .xlsx file.')

    table = []
    empty_run = 0
    for number, values in raw_rows:
        cells = [_cell(v) for v in values]
        if any(cells):
            empty_run = 0
            table.append((number, cells))
            if len(table) > MAX_ROWS + 1:  # + the header row
                raise ImportFileError(f'The file has more than {MAX_ROWS} students. Split it into smaller files.')
        else:
            empty_run += 1
            if table and empty_run > MAX_ROWS:  # only empty (formatted) rows below the data
                break
    if not table:
        raise ImportFileError('The file is empty.')
    return table


def _read_csv(raw: bytes):
    try:
        text = raw.decode('utf-8-sig')  # Excel's "CSV UTF-8" starts with a BOM
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    try:
        yield from enumerate(csv.reader(io.StringIO(text), dialect), start=1)
    except csv.Error:
        raise ImportFileError('This CSV file could not be read.')


def _read_xlsx(upload):
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(upload, read_only=True, data_only=True)
    except Exception:
        raise ImportFileError('This Excel file could not be read. Save it as .xlsx and try again.')
    try:
        yield from enumerate(workbook.worksheets[0].iter_rows(values_only=True), start=1)
    except Exception:
        raise ImportFileError('This Excel file could not be read. Save it as .xlsx and try again.')
    finally:
        workbook.close()


def _columns(header):
    columns = {}
    for index, title in enumerate(header):
        key = HEADER_ALIASES.get(re.sub(r'[\s\-.]+', '_', title.strip().lower()).strip('_'))
        if key and key not in columns:
            columns[key] = index
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if 'name' not in columns and 'first_name' not in columns:
        missing.insert(1, 'name')
    if missing:
        raise ImportFileError(
            f'The file is missing the column{"s" if len(missing) > 1 else ""} {", ".join(missing)}. '
            'The first row must name the columns: student_id, name, email, level, term.'
        )
    return columns


# ── Checking the rows ─────────────────────────────────────────────────────────


def _parse_student_id(text):
    try:
        value = int(text)
    except ValueError:
        try:
            number = float(text)
        except ValueError:
            return None
        if not number.is_integer():
            return None
        value = int(number)
    return value if 0 < value <= MAX_STUDENT_ID else None


def _split_name(full_name):
    full_name = ' '.join(full_name.split())
    if ' ' not in full_name:
        return full_name, ''
    first, last = full_name.rsplit(' ', 1)
    return first, last


def plan_import(table):
    """(rows for the response, rows to create). Writes nothing."""
    header_number, header = table[0]
    columns = _columns(header)

    def value(cells, key):
        index = columns.get(key)
        return cells[index].strip() if index is not None and index < len(cells) else ''

    rows = []
    for number, cells in table[1:]:
        errors = []
        sid_text = value(cells, 'student_id')
        student_id = _parse_student_id(sid_text) if sid_text else None
        if not sid_text:
            errors.append('Student ID is missing.')
        elif student_id is None:
            errors.append('Student ID must be a whole number.')

        if 'name' in columns and value(cells, 'name'):
            first_name, last_name = _split_name(value(cells, 'name'))
        else:
            first_name, last_name = value(cells, 'first_name'), value(cells, 'last_name')
        name = ' '.join(f'{first_name} {last_name}'.split())
        if not first_name:
            errors.append('Name is missing.')
        elif len(first_name) > 150 or len(last_name) > 150:
            errors.append('Name is too long.')

        email = value(cells, 'email').lower()
        if not email:
            errors.append('Email is missing.')
        else:
            try:
                validate_email(email)
            except DjangoValidationError:
                errors.append('Email is not valid.')

        level_text, term_text = value(cells, 'level'), value(cells, 'term')
        level = LEVEL_WORDS.get(level_text.lower())
        term = TERM_WORDS.get(term_text.lower())
        if level is None:
            errors.append('Level must be First, Second, Third or Fourth.' if level_text else 'Level is missing.')
        if term is None:
            errors.append('Term must be I or II.' if term_text else 'Term is missing.')

        rows.append({
            'row': number,
            'student_id': student_id if student_id is not None else sid_text,
            'name': name,
            'email': email,
            'level': level or level_text,
            'term': term or term_text,
            'status': 'error' if errors else 'create',
            'errors': errors,
            '_first_name': first_name,
            '_last_name': last_name,
        })

    # The same student twice in the file
    first_row_with = {}
    for row in rows:
        if row['status'] == 'error':
            continue
        for key, label in (('student_id', 'student ID'), ('email', 'email')):
            seen = first_row_with.setdefault((key, row[key]), row['row'])
            if seen != row['row']:
                row['errors'].append(f'Same {label} as row {seen}.')
        if row['errors']:
            row['status'] = 'error'

    # Already in the portal
    candidates = [r for r in rows if r['status'] == 'create']
    sids = {r['student_id'] for r in candidates}
    emails = {r['email'] for r in candidates}
    existing_sids = set(StudentProfile.objects.filter(student_id__in=sids).values_list('student_id', flat=True))
    email_roles = {}
    for email_lower, username_lower, role in (
        User.objects.annotate(email_lower=Lower('email'), username_lower=Lower('username'))
        .filter(Q(email_lower__in=emails) | Q(username_lower__in=emails))
        .values_list('email_lower', 'username_lower', 'role')
    ):
        for key in (email_lower, username_lower):
            if key in emails and email_roles.get(key) != User.Role.STUDENT:
                email_roles[key] = role
    for row in candidates:
        role = email_roles.get(row['email'])
        if row['student_id'] in existing_sids or role == User.Role.STUDENT:
            row['status'] = 'exists'
        elif role is not None:
            row['status'] = 'error'
            row['errors'].append('This email belongs to a teacher or admin account.')

    to_create = [r for r in rows if r['status'] == 'create']
    public_rows = [{k: v for k, v in r.items() if not k.startswith('_')} for r in rows]
    return public_rows, to_create


# ── Writing ───────────────────────────────────────────────────────────────────


def semester_classroom(semester):
    """The semester's class group ("Main"), made if the semester has none yet."""
    classrooms = semester.classrooms.filter(deleted=False)
    classroom = classrooms.filter(name='Main').first() or classrooms.order_by('name').first()
    if classroom is None:
        classroom = Classroom.objects.create(name='Main', semester=semester)
    return classroom


def apply_import(to_create, semester=None):
    """Creates the accounts in one transaction. [{student_id, email, temporary_password}]."""
    created = []
    with transaction.atomic():
        classroom = semester_classroom(semester) if semester is not None else None
        for row in to_create:
            password = temporary_password()
            user = User.objects.create(
                username=row['email'],
                email=row['email'],
                first_name=row['_first_name'],
                last_name=row['_last_name'],
                role=User.Role.STUDENT,
                faculty=FACULTY,
                department=DEPARTMENT,
                is_verified=True,
                password=temporary_password_hash(password),
            )
            profile = StudentProfile.objects.create(
                user=user, student_id=row['student_id'], current_level=row['level'], current_semester=row['term'],
            )
            if classroom is not None:
                StudentClassroom.objects.get_or_create(student=profile, classroom=classroom)
            created.append({'student_id': row['student_id'], 'email': row['email'], 'temporary_password': password})
    return created
