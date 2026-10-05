from rest_framework.permissions import BasePermission

from config.errors import error_response


class IsAdmin(BasePermission):
    message = 'Only admins can do this.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'ADMIN'


class IsTeacher(BasePermission):
    message = 'Only teachers can do this.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'TEACHER'


class IsStudent(BasePermission):
    message = 'Only students can do this.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'STUDENT'


class IsAdminOrTeacher(BasePermission):
    message = 'Only teachers and admins can do this.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ('ADMIN', 'TEACHER')


def can_manage_course(user, course_info) -> bool:
    """Admins manage every course; a teacher only the courses assigned to them."""
    if user.role == 'ADMIN':
        return True
    return (
        user.role == 'TEACHER'
        and course_info.teacher_id is not None
        and course_info.teacher.user_id == user.id
    )


def not_your_course_response():
    return error_response('You do not teach this course.', status=403, code='permission_denied')
