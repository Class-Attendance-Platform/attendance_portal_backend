"""Shared test helpers: tiny factories for users, courses and live sessions."""
import json
import time

from django.core.cache import cache
from rest_framework.test import APIClient

from apps.academic.models import Classroom, Course, CourseInfo, Semester, StudentClassroom
from apps.attendance import redis_service
from apps.users.models import AdminProfile, StudentProfile, TeacherProfile, User

PASSWORD = 'Test-pass-123'


def make_user(email, role, **extra):
    extra.setdefault('is_verified', True)
    return User.objects.create_user(
        username=email, email=email, password=PASSWORD, role=role, **extra
    )


def make_student(email, student_id, **user_extra):
    user_extra.setdefault('first_name', 'Student')
    user_extra.setdefault('last_name', str(student_id))
    user = make_user(email, User.Role.STUDENT, **user_extra)
    return StudentProfile.objects.create(
        user=user, student_id=student_id, current_level='Third', current_semester='I'
    )


def make_teacher(email, **user_extra):
    user_extra.setdefault('first_name', 'Teacher')
    user = make_user(email, User.Role.TEACHER, **user_extra)
    return TeacherProfile.objects.create(user=user, employee_id=email)


def make_admin(email):
    user = make_user(email, User.Role.ADMIN)
    AdminProfile.objects.create(user=user)
    return user


def make_semester(level='Third', term='I', session='2025-26', students=(), **extra):
    """A semester with its "Main" class group (students joined from the start)."""
    semester = Semester.objects.create(level=level, semester=term, session=session, **extra)
    classroom = Classroom.objects.create(name='Main', semester=semester)
    for student in students:
        StudentClassroom.objects.create(student=student, classroom=classroom)
    return semester


def make_course_info(teacher, code='CSE301', students=(), semester=None):
    """A course taught on `semester`'s class group (a new Level 3 Term I semester if None)."""
    if semester is None:
        semester = make_semester(students=students, session='')
    elif students:
        raise ValueError('Add students to the semester instead.')
    course = Course.objects.create(code=code, title=f'Course {code}')
    classroom = Classroom.objects.get(semester=semester, name='Main')
    return CourseInfo.objects.create(
        course=course, teacher=teacher, semester=semester, classroom=classroom
    )


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def expire_session(session_id):
    """Move a live session's end time into the past, as if the timer ran out."""
    key = redis_service._key(str(session_id))
    data = json.loads(cache.get(key))
    data['end_time'] = time.time() - 1
    cache.set(key, json.dumps(data), timeout=3600)


def start_session(client, course_info, **body):
    """Starts a live session (5 minutes, in class unless `body` says otherwise); returns the response."""
    body.setdefault('duration_minutes', 5)
    return client.post('/api/sessions/start/', {'course_info_id': str(course_info.id), **body}, format='json')


def current_code(session_id):
    """The session's current 6-digit code (what the teacher's screen shows)."""
    from apps.attendance import codes
    from apps.attendance.models import AttendanceSession
    return codes.current_code(AttendanceSession.objects.get(id=session_id).qr_token)[0]


def check_in(student, session_id=None, code=None, device_id=None, by_qr=True):
    """A student's check-in: by the QR link (session id + code) or, with by_qr=False, the typed code."""
    if code is None:
        code = current_code(session_id)
    body = {'code': code, 'device_id': device_id or f'device-{student.student_id}'}
    if by_qr and session_id is not None:
        body['session_id'] = str(session_id)
    return client_for(student.user).post('/api/sessions/check-in/', body, format='json')
