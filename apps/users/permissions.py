from rest_framework.permissions import BasePermission
from rest_framework.response import Response


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'ADMIN'


class IsTeacher(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'TEACHER'


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'STUDENT'


class IsAdminOrTeacher(BasePermission):
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
    return Response({'success': False, 'message': 'You do not teach this course.'}, status=403)
