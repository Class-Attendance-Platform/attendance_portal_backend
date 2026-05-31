from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.users.permissions import IsTeacher, IsAdminOrTeacher
from apps.users.models import TeacherProfile, StudentProfile
from apps.academic.models import CourseInfo, Semester
from apps.academic.serializers import CourseInfoSerializer, StudentInClassroomSerializer


class TeacherCoursesView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        teacher = get_object_or_404(TeacherProfile, id=uuid, user__deleted=False)

        all_cis = CourseInfo.objects.filter(
            teacher=teacher, deleted=False
        ).select_related('course', 'semester', 'classroom')

        # Split active vs previous semesters
        current = [ci for ci in all_cis if ci.semester.is_active]
        previous = [ci for ci in all_cis if not ci.semester.is_active]

        def serialize(ci):
            return {
                'id': str(ci.id),
                'course': {
                    'id': str(ci.course.id),
                    'code': ci.course.code,
                    'title': ci.course.title,
                    'credits': ci.course.credits,
                },
                'semester': {
                    'id': str(ci.semester.id),
                    'level': ci.semester.level,
                    'semester': ci.semester.semester,
                },
                'classroom': {
                    'id': str(ci.classroom.id),
                    'name': ci.classroom.name,
                },
            }

        return Response({
            'success': True,
            'current_courses': [serialize(ci) for ci in current],
            'previous_courses': [serialize(ci) for ci in previous],
        })


class TeacherCourseInfoDetailView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci = get_object_or_404(
            CourseInfo.objects.select_related('course', 'teacher__user', 'semester', 'classroom'),
            id=uuid, deleted=False
        )

        # Get enrolled students via classroom
        memberships = ci.classroom.memberships.select_related('student__user')
        students = [m.student for m in memberships]

        return Response({
            'success': True,
            'course_info': {
                'id': str(ci.id),
                'course': {
                    'id': str(ci.course.id),
                    'code': ci.course.code,
                    'title': ci.course.title,
                    'credits': ci.course.credits,
                },
                'teacher': {
                    'id': str(ci.teacher.id) if ci.teacher else None,
                    'name': ci.teacher.user.get_full_name() if ci.teacher else None,
                    'email': ci.teacher.user.email if ci.teacher else None,
                },
                'semester': {
                    'id': str(ci.semester.id),
                    'level': ci.semester.level,
                    'semester': ci.semester.semester,
                    'is_active': ci.semester.is_active,
                },
                'classroom': {
                    'id': str(ci.classroom.id),
                    'name': ci.classroom.name,
                },
                'students': StudentInClassroomSerializer(students, many=True).data,
            }
        })
