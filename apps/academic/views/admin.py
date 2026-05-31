from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from apps.users.permissions import IsAdmin
from apps.users.models import StudentProfile, TeacherProfile
from apps.academic.models import Semester, Course, Classroom, StudentClassroom, CourseInfo
from apps.academic.serializers import (
    SemesterSerializer, CourseSerializer,
    ClassroomSerializer, ClassroomStudentBulkSerializer,
    StudentInClassroomSerializer, CourseInfoSerializer,
    PromoteStudentsSerializer,
)


# ── Semesters ─────────────────────────────────────────────────────────────────

class SemesterListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        semesters = Semester.objects.filter(deleted=False)
        return Response({'success': True, 'semesters': SemesterSerializer(semesters, many=True).data})

    def post(self, request):
        serializer = SemesterSerializer(data=request.data)
        if serializer.is_valid():
            semester = serializer.save()
            return Response({'success': True, 'semester': SemesterSerializer(semester).data}, status=201)
        return Response({'success': False, 'errors': serializer.errors}, status=400)


class SemesterDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = SemesterSerializer(semester, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'semester': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=400)

    def delete(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid)
        semester.deleted = True
        semester.is_active = False
        semester.save()
        return Response({'success': True, 'message': 'Semester deleted.'})


# ── Courses ───────────────────────────────────────────────────────────────────

class CourseListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        courses = Course.objects.filter(deleted=False)
        return Response({'success': True, 'courses': CourseSerializer(courses, many=True).data})

    def post(self, request):
        serializer = CourseSerializer(data=request.data)
        if serializer.is_valid():
            course = serializer.save()
            return Response({'success': True, 'course': CourseSerializer(course).data}, status=201)
        return Response({'success': False, 'errors': serializer.errors}, status=400)


class CourseDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        course = get_object_or_404(Course, id=uuid, deleted=False)
        serializer = CourseSerializer(course, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'course': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=400)

    def delete(self, request, uuid):
        course = get_object_or_404(Course, id=uuid)
        course.deleted = True
        course.save()
        return Response({'success': True, 'message': 'Course deleted.'})


# ── Classrooms ────────────────────────────────────────────────────────────────

class ClassroomListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        classrooms = Classroom.objects.filter(deleted=False).select_related('semester')
        return Response({'success': True, 'classrooms': ClassroomSerializer(classrooms, many=True).data})

    def post(self, request):
        serializer = ClassroomSerializer(data=request.data)
        if serializer.is_valid():
            classroom = serializer.save()
            return Response({'success': True, 'classroom': ClassroomSerializer(classroom).data}, status=201)
        return Response({'success': False, 'errors': serializer.errors}, status=400)


class ClassroomDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = ClassroomSerializer(classroom, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'classroom': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=400)

    def delete(self, request, uuid):
        classroom = get_object_or_404(Classroom, id=uuid)
        classroom.deleted = True
        classroom.save()
        return Response({'success': True, 'message': 'Classroom deleted.'})


# ── Classroom Students ────────────────────────────────────────────────────────

class ClassroomStudentsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        memberships = classroom.memberships.select_related('student__user')
        students = [m.student for m in memberships]
        return Response({
            'success': True,
            'classroom': str(classroom.id),
            'students': StudentInClassroomSerializer(students, many=True).data
        })

    def post(self, request, uuid):
        """Add students to classroom."""
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = ClassroomStudentBulkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        student_ids = serializer.validated_data['student_ids']
        added, already_in, not_found = [], [], []

        for sid in student_ids:
            try:
                student = StudentProfile.objects.get(id=sid, user__deleted=False)
                _, created = StudentClassroom.objects.get_or_create(student=student, classroom=classroom)
                if created:
                    added.append(str(sid))
                else:
                    already_in.append(str(sid))
            except StudentProfile.DoesNotExist:
                not_found.append(str(sid))

        return Response({
            'success': True,
            'added': added,
            'already_in_classroom': already_in,
            'not_found': not_found,
        })

    def delete(self, request, uuid):
        """Remove students from classroom."""
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = ClassroomStudentBulkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        student_ids = serializer.validated_data['student_ids']
        removed, not_found = [], []

        for sid in student_ids:
            deleted_count, _ = StudentClassroom.objects.filter(
                student__id=sid, classroom=classroom
            ).delete()
            if deleted_count:
                removed.append(str(sid))
            else:
                not_found.append(str(sid))

        return Response({'success': True, 'removed': removed, 'not_found': not_found})


class ClassroomPromoteView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        """Bulk promote students to a new level/semester."""
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = PromoteStudentsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        new_level = serializer.validated_data['new_level']
        new_semester = serializer.validated_data['new_semester']
        student_ids = serializer.validated_data.get('student_ids')

        if student_ids:
            students = StudentProfile.objects.filter(
                id__in=student_ids,
                classroom_memberships__classroom=classroom
            )
        else:
            students = StudentProfile.objects.filter(
                classroom_memberships__classroom=classroom
            )

        count = students.update(current_level=new_level, current_semester=new_semester)
        return Response({
            'success': True,
            'promoted_count': count,
            'new_level': new_level,
            'new_semester': new_semester,
        })


# ── CourseInfo ────────────────────────────────────────────────────────────────

class CourseInfoListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        course_infos = CourseInfo.objects.filter(deleted=False).select_related(
            'course', 'teacher__user', 'semester', 'classroom'
        )
        return Response({'success': True, 'course_infos': CourseInfoSerializer(course_infos, many=True).data})

    def post(self, request):
        serializer = CourseInfoSerializer(data=request.data)
        if serializer.is_valid():
            ci = serializer.save()
            return Response(
                {'success': True, 'course_info': CourseInfoSerializer(ci).data},
                status=201
            )
        return Response({'success': False, 'errors': serializer.errors}, status=400)


class CourseInfoDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        serializer = CourseInfoSerializer(ci, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'course_info': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=400)

    def delete(self, request, uuid):
        ci = get_object_or_404(CourseInfo, id=uuid)
        ci.deleted = True
        ci.save()
        return Response({'success': True, 'message': 'CourseInfo deleted.'})
