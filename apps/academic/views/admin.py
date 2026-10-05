import uuid as uuid_lib
from collections import Counter

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import IsAdmin
from apps.users.models import StudentProfile, TeacherProfile
from apps.academic import services
from apps.academic.models import (
    LEVEL_NUMBERS, Semester, Course, Classroom, StudentClassroom, CourseInfo, sort_semesters,
)
from apps.academic.serializers import (
    CourseInfoTeacherSerializer, CourseSerializer, ProfileIdsSerializer, PromoteSerializer,
    SemesterCourseSerializer, SemesterCreateSerializer, SemesterUpdateSerializer,
    admin_course_info_row, roster_row, semester_course_row, semester_row,
    ClassroomSerializer, ClassroomStudentBulkSerializer,
    StudentInClassroomSerializer, CourseInfoSerializer,
    PromoteStudentsSerializer,
)
from apps.academic.stats import course_numbers, semester_numbers
from apps.attendance.models import AttendanceLog, AttendanceSession
from config.errors import error_response, validation_error_response

User = get_user_model()


def _level_taken(level):
    return error_response(
        f'Level {LEVEL_NUMBERS.get(level, level)} already has an active semester. Finish it first.',
        400, code='level_has_active_semester',
    )


def _true(value) -> bool:
    return str(value or '').strip().lower() in ('true', '1', 'yes')


# ── Semesters ─────────────────────────────────────────────────────────────────

SEMESTER_STATUS = {
    'all': Q(deleted=False),
    'active': Q(deleted=False, is_active=True),
    'finished': Q(deleted=False, is_active=False),
    'deleted': Q(deleted=True),
}


def semester_rows(semesters) -> list:
    """Rows in the given order; counts in a fixed number of queries."""
    semesters = list(semesters)
    rooms = services.main_classrooms(semesters)
    active = {s.id: s.is_active for s in semesters}
    students = Counter()
    members = (
        StudentClassroom.objects.filter(classroom__in=list(rooms.values())).active_accounts()
        .values_list('classroom__semester_id', 'left_at')
    )
    for semester_id, left_at in members:
        if left_at is None or not active[semester_id]:  # the class list (see MembershipQuerySet)
            students[semester_id] += 1
    courses = dict(
        CourseInfo.objects.filter(semester__in=semesters, deleted=False, course__deleted=False)
        .values('semester_id').annotate(n=Count('id')).values_list('semester_id', 'n')
    )
    return [
        semester_row(s, rooms.get(s.id), students[s.id], courses.get(s.id, 0)) for s in semesters
    ]


def semester_payload(semester) -> dict:
    return semester_rows([semester])[0]


class SemesterListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        value = (request.query_params.get('status') or 'all').strip().lower()
        if value not in SEMESTER_STATUS:
            return error_response(
                'Status must be "active", "finished", "deleted" or "all".', 400, code='invalid_status',
            )
        semesters = sort_semesters(Semester.objects.filter(SEMESTER_STATUS[value]))
        return Response({'success': True, 'semesters': semester_rows(semesters)})

    def post(self, request):
        """A new active semester and its class group."""
        serializer = SemesterCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        if services.level_has_active_semester(data['level']):
            return _level_taken(data['level'])
        semester = services.create_semester(
            data['level'], data['semester'], data['session'], data.get('start_date'), data.get('end_date'),
        )
        return Response({'success': True, 'semester': semester_payload(semester)}, status=status.HTTP_201_CREATED)


class SemesterDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid)
        return Response({'success': True, 'semester': semester_payload(semester)})

    def patch(self, request, uuid):
        """Partial: session, start_date, end_date."""
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = SemesterUpdateSerializer(semester, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()
        return Response({'success': True, 'semester': semester_payload(semester)})

    put = patch  # the old app sends PUT (its students/courses lists are ignored)

    def delete(self, request, uuid):
        """Soft delete; it comes back finished with POST .../restore/."""
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        semester.deleted = True
        semester.is_active = False
        semester.save(update_fields=['deleted', 'is_active'])
        return Response({'success': True, 'message': 'Semester deleted.'})


class SemesterFinishView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        if semester.is_active:
            semester.is_active = False
            semester.save(update_fields=['is_active'])
        return Response({'success': True, 'message': 'Semester finished.', 'semester': semester_payload(semester)})


class SemesterReopenView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        if not semester.is_active:
            if services.level_has_active_semester(semester.level, exclude=semester):
                return _level_taken(semester.level)
            semester.is_active = True
            semester.save(update_fields=['is_active'])
        return Response({'success': True, 'message': 'Semester reopened.', 'semester': semester_payload(semester)})


class SemesterRestoreView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid)
        if semester.deleted:
            semester.deleted = False
            semester.save(update_fields=['deleted'])
        return Response({'success': True, 'message': 'Semester restored.', 'semester': semester_payload(semester)})


# ── Semester roster ───────────────────────────────────────────────────────────


def _profiles_or_error(profile_ids):
    """(profiles, None) or (None, 400 response) when an id is unknown or a deleted account."""
    wanted = set(profile_ids)
    profiles = list(StudentProfile.objects.filter(id__in=wanted, user__deleted=False))
    if len(profiles) != len(wanted):
        return None, validation_error_response({'profile_ids': ['Some of these students were not found.']})
    return profiles, None


def _students_message(verb, count):
    return f'{count} student{"" if count == 1 else "s"} {verb}.'


class SemesterStudentsView(APIView):
    """Members of the semester's class group: current first; ?include_left=true adds former ones."""
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid)
        classroom = services.main_classroom(semester, create=False)
        if classroom is None:
            return Response({'success': True, 'students': []})
        members = (
            StudentClassroom.objects.filter(classroom=classroom).active_accounts()
            .select_related('student__user').order_by('student__student_id')
        )
        if not _true(request.query_params.get('include_left')):
            members = members.current()
        members = sorted(members, key=lambda m: m.left_at is not None)  # stable: current first
        return Response({'success': True, 'students': [roster_row(m) for m in members]})

    def post(self, request, uuid):
        """Adds students (joined today; a former member comes back with the original joined_at)."""
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = ProfileIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        profiles, problem = _profiles_or_error(serializer.validated_data['profile_ids'])
        if problem:
            return problem
        with transaction.atomic():
            result = services.add_members(
                services.main_classroom(semester), profiles, services.join_date(semester),
            )
        return Response({
            'success': True,
            'message': _students_message('added', result['added'] + result['rejoined']),
            **result,
        })


class SemesterStudentsRemoveView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        """Marks students as left (today, or tomorrow after a class today); their history stays."""
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = ProfileIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        removed = services.remove_members(
            services.main_classroom(semester, create=False), serializer.validated_data['profile_ids'],
        )
        return Response({'success': True, 'message': _students_message('removed', removed), 'removed': removed})


# ── Courses taught in a semester ──────────────────────────────────────────────


def _semester_course_infos(semester):
    return (
        CourseInfo.objects.filter(semester=semester, deleted=False, course__deleted=False)
        .select_related('course', 'teacher__user').order_by('course__code')
    )


def _teacher_or_error(teacher_id):
    """(teacher or None, None) or (None, 400 response)."""
    if teacher_id is None:
        return None, None
    teacher = TeacherProfile.objects.select_related('user').filter(id=teacher_id, user__deleted=False).first()
    if teacher is None:
        return None, validation_error_response({'teacher_id': ['This teacher was not found.']})
    return teacher, None


class SemesterCoursesView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        semester = get_object_or_404(Semester, id=uuid)
        return Response({
            'success': True,
            'courses': [semester_course_row(ci) for ci in _semester_course_infos(semester)],
        })

    def post(self, request, uuid):
        """Teaches a course in this semester (its class group); teacher_id may be null."""
        semester = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = SemesterCourseSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        course = Course.objects.filter(id=data['course_id'], deleted=False).first()
        if course is None:
            return validation_error_response({'course_id': ['This course was not found.']})
        teacher, problem = _teacher_or_error(data.get('teacher_id'))
        if problem:
            return problem
        if CourseInfo.objects.filter(semester=semester, course=course, deleted=False).exists():
            return validation_error_response(
                {'course_id': [f'{course.code} is already taught in this semester.']}, code='course_already_added',
            )

        try:
            with transaction.atomic():
                classroom = services.main_classroom(semester)
                # A course removed from this semester earlier comes back with its attendance history
                # (the one with the same teacher if there are several).
                earlier = list(CourseInfo.objects.filter(semester=semester, course=course, deleted=True))
                teacher_id = teacher.id if teacher else None
                ci = next((c for c in earlier if c.teacher_id == teacher_id), earlier[0] if earlier else None)
                if ci is None:
                    ci = CourseInfo.objects.create(course=course, teacher=teacher, semester=semester, classroom=classroom)
                else:
                    ci.teacher, ci.classroom, ci.deleted = teacher, classroom, False
                    ci.save(update_fields=['teacher', 'classroom', 'deleted'])
        except IntegrityError:
            return error_response('This could not be saved. Please try again.', 409, code='conflict')
        ci = _semester_course_infos(semester).get(id=ci.id)
        return Response({
            'success': True, 'message': f'{course.code} added.', 'course': semester_course_row(ci),
        }, status=status.HTTP_201_CREATED)


# ── Promote ───────────────────────────────────────────────────────────────────


class SemesterPromoteView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        """Moves students to the next semester and finishes this one (never deletes it)."""
        source = get_object_or_404(Semester, id=uuid, deleted=False)
        serializer = PromoteSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data

        profile_ids = data.get('profile_ids')
        if profile_ids is not None:
            members = set(
                StudentClassroom.objects.filter(
                    classroom=services.main_classroom(source, create=False), student_id__in=profile_ids,
                ).current().active_accounts().values_list('student_id', flat=True)
            )
            if members != set(profile_ids):
                return validation_error_response({'profile_ids': ['Some of these students are not in this semester.']})

        with transaction.atomic():
            if data.get('target_semester_id'):
                target = Semester.objects.filter(id=data['target_semester_id'], deleted=False).first()
                if target is None:
                    return validation_error_response({'target_semester_id': ['This semester was not found.']})
            else:
                wanted = data['target']
                target = sort_semesters(Semester.objects.filter(
                    level=wanted['level'], semester=wanted['semester'], session=wanted['session'], deleted=False,
                ))
                target = target[0] if target else None
                if target is None:
                    if services.level_has_active_semester(wanted['level'], exclude=source):
                        return _level_taken(wanted['level'])
                    target = services.create_semester(
                        wanted['level'], wanted['semester'], wanted['session'],
                        wanted.get('start_date'), wanted.get('end_date'),
                    )
            if target.id == source.id:
                return validation_error_response(
                    {'target_semester_id': ['Choose a different semester to move the students to.']},
                )
            moved = services.promote(source, target, profile_ids)

        return Response({
            'success': True,
            'message': f'{moved} student{"" if moved == 1 else "s"} moved to {target.label}.',
            'target_semester_id': str(target.id),
            'moved': moved,
        })


# ── Courses ───────────────────────────────────────────────────────────────────

COURSE_STATUS = {'active': False, 'deleted': True}


class CourseListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        value = (request.query_params.get('status') or 'active').strip().lower()
        if value not in COURSE_STATUS:
            return error_response('Status must be "active" or "deleted".', 400, code='invalid_status')
        courses = Course.objects.filter(deleted=COURSE_STATUS[value])
        return Response({'success': True, 'courses': CourseSerializer(courses, many=True).data})

    def post(self, request):
        serializer = CourseSerializer(data=request.data)
        if serializer.is_valid():
            course = serializer.save()
            return Response({'success': True, 'course': CourseSerializer(course).data}, status=201)
        return validation_error_response(serializer.errors)


class CourseDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        course = get_object_or_404(Course, id=uuid)
        return Response({'success': True, 'course': CourseSerializer(course).data})

    def patch(self, request, uuid):
        """Partial: any of code, title, content, credits."""
        course = get_object_or_404(Course, id=uuid, deleted=False)
        serializer = CourseSerializer(course, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        try:
            serializer.save()
        except IntegrityError:
            return validation_error_response({'code': ['A course with this code already exists.']})
        return Response({'success': True, 'course': serializer.data})

    put = patch  # the old app sends PUT

    def delete(self, request, uuid):
        """Soft delete: hidden from admins' lists, teachers and students until restored."""
        course = get_object_or_404(Course, id=uuid, deleted=False)
        course.deleted = True
        course.save(update_fields=['deleted'])
        return Response({'success': True, 'message': 'Course deleted.'})


class CourseRestoreView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        course = get_object_or_404(Course, id=uuid)
        if course.deleted:
            course.deleted = False
            course.save(update_fields=['deleted'])
        return Response({'success': True, 'message': 'Course restored.', 'course': CourseSerializer(course).data})


# ── Overview ──────────────────────────────────────────────────────────────────


class AdminOverviewView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        people = {'user__deleted': False, 'user__is_verified': True}
        active_semesters = sort_semesters(Semester.objects.filter(is_active=True, deleted=False))
        counts = {
            'students': StudentProfile.objects.filter(**people).count(),
            'teachers': TeacherProfile.objects.filter(**people).count(),
            'courses': Course.objects.filter(deleted=False).count(),
            'active_semesters': len(active_semesters),
            'pending_approvals': User.objects.filter(
                is_verified=False, deleted=False, role__in=(User.Role.STUDENT, User.Role.TEACHER),
            ).count(),
        }

        numbers = semester_numbers(active_semesters)
        semesters = [
            {
                'id': row['id'],
                'label': row['label'],
                'student_count': row['student_count'],
                'course_count': row['course_count'],
                **numbers[semester.id],
            }
            for semester, row in zip(active_semesters, semester_rows(active_semesters))
        ]

        sessions = (
            AttendanceSession.objects.filter(
                is_active=False, course_info__deleted=False, course_info__course__deleted=False,
                course_info__semester__deleted=False,
            )
            .select_related('course_info__course')
            .annotate(
                present=Count('logs', filter=Q(logs__status=AttendanceLog.Status.PRESENT)),
                total=Count('logs'),
            )
            .order_by(F('ended_at').desc(nulls_last=True), '-started_at')[:10]
        )
        recent = [
            {
                'session_id': str(s.id),
                'course_info_id': str(s.course_info_id),
                'course_code': s.course_info.course.code,
                'course_title': s.course_info.course.title,
                'date': s.date.isoformat(),
                'delivery': s.delivery,
                'mode': s.mode,
                'present': s.present,
                'total': s.total,
            }
            for s in sessions
        ]
        return Response({'success': True, 'counts': counts, 'semesters': semesters, 'recent_sessions': recent})


# ── Classrooms (older endpoints; the redesigned app never shows classrooms) ───

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
        return validation_error_response(serializer.errors)


class ClassroomDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, uuid):
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = ClassroomSerializer(classroom, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'classroom': serializer.data})
        return validation_error_response(serializer.errors)

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
        memberships = classroom.memberships.current().select_related('student__user')
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
            return validation_error_response(serializer.errors)

        student_ids = serializer.validated_data['student_ids']
        students = {s.id: s for s in StudentProfile.objects.filter(id__in=student_ids, user__deleted=False)}
        already = set(classroom.memberships.current().filter(student_id__in=students).values_list('student_id', flat=True))
        services.add_members(classroom, students.values(), services.join_date(classroom.semester))

        return Response({
            'success': True,
            'added': [str(sid) for sid in student_ids if sid in students and sid not in already],
            'already_in_classroom': [str(sid) for sid in student_ids if sid in already],
            'not_found': [str(sid) for sid in student_ids if sid not in students],
        })

    def delete(self, request, uuid):
        """Remove students from classroom (they are marked as left; history stays)."""
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = ClassroomStudentBulkSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        student_ids = serializer.validated_data['student_ids']
        current = set(classroom.memberships.current().filter(student_id__in=student_ids).values_list('student_id', flat=True))
        services.remove_members(classroom, current)
        return Response({
            'success': True,
            'removed': [str(sid) for sid in student_ids if sid in current],
            'not_found': [str(sid) for sid in student_ids if sid not in current],
        })


class ClassroomPromoteView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        """Bulk promote students to a new level/semester (older endpoint; see SemesterPromoteView)."""
        classroom = get_object_or_404(Classroom, id=uuid, deleted=False)
        serializer = PromoteStudentsSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        new_level = serializer.validated_data['new_level']
        new_semester = serializer.validated_data['new_semester']
        student_ids = serializer.validated_data.get('student_ids')

        members = classroom.memberships.current().values_list('student_id', flat=True)
        students = StudentProfile.objects.filter(id__in=members)
        if student_ids:
            students = students.filter(id__in=student_ids)

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
        """Every taught course (?semester_id= for one semester) with its attendance numbers."""
        course_infos = CourseInfo.objects.filter(
            deleted=False, course__deleted=False, semester__deleted=False,
        ).select_related('course', 'teacher__user', 'semester')
        semester_id = (request.query_params.get('semester_id') or '').strip()
        if semester_id:
            try:
                course_infos = course_infos.filter(semester_id=uuid_lib.UUID(semester_id))
            except ValueError:
                return validation_error_response({'semester_id': ['This semester was not found.']})
        course_infos = list(course_infos)
        order = {s.id: i for i, s in enumerate(sort_semesters({ci.semester for ci in course_infos}))}
        course_infos.sort(key=lambda ci: (order[ci.semester_id], ci.course.code))
        numbers = course_numbers(course_infos)
        return Response({
            'success': True,
            'course_infos': [admin_course_info_row(ci, numbers[ci.id]) for ci in course_infos],
        })

    def post(self, request):
        """Older endpoint; the redesigned app adds courses with POST /admin/semesters/<id>/courses/."""
        serializer = CourseInfoSerializer(data=request.data)
        if serializer.is_valid():
            ci = serializer.save()
            return Response(
                {'success': True, 'course_info': CourseInfoSerializer(ci).data},
                status=201
            )
        return validation_error_response(serializer.errors)


class CourseInfoDetailView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, uuid):
        """Reassigns the teacher (null = none); the new teacher sees all of the course's history."""
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        serializer = CourseInfoTeacherSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        teacher, problem = _teacher_or_error(serializer.validated_data['teacher_id'])
        if problem:
            return problem
        ci.teacher = teacher
        try:
            with transaction.atomic():
                ci.save(update_fields=['teacher'])
        except IntegrityError:
            return error_response(
                'This teacher already has this course in this semester.', 409, code='conflict',
            )
        ci = CourseInfo.objects.select_related('course', 'teacher__user').get(id=ci.id)
        return Response({
            'success': True,
            'message': f'{ci.course.code}: teacher changed.',
            'course_info': semester_course_row(ci),
        })

    put = patch

    def delete(self, request, uuid):
        """Soft delete (its attendance stays; adding the course again brings it back)."""
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        ci.deleted = True
        ci.save(update_fields=['deleted'])
        return Response({'success': True, 'message': 'Course removed from the semester.'})
