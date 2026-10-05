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
    return User.objects.create_user(
        username=email, email=email, password=PASSWORD, role=role, is_verified=True, **extra
    )


def make_student(email, student_id):
    user = make_user(email, User.Role.STUDENT, first_name='Student', last_name=str(student_id))
    return StudentProfile.objects.create(
        user=user, student_id=student_id, current_level='Third', current_semester='I'
    )


def make_teacher(email):
    user = make_user(email, User.Role.TEACHER, first_name='Teacher')
    return TeacherProfile.objects.create(user=user, employee_id=email)


def make_admin(email):
    user = make_user(email, User.Role.ADMIN)
    AdminProfile.objects.create(user=user)
    return user


def make_course_info(teacher, code='CSE301', students=()):
    semester = Semester.objects.create(level='Third', semester='I')
    course = Course.objects.create(code=code, title=f'Course {code}')
    classroom = Classroom.objects.create(name='Main', semester=semester)
    for student in students:
        StudentClassroom.objects.create(student=student, classroom=classroom)
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
