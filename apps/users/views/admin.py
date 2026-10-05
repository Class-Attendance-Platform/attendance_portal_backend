import uuid as uuid_lib
from collections import Counter

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import CharField, Count, Exists, OuterRef, Q
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import Semester, StudentClassroom
from apps.faces.models import StudentFace
from apps.users.importing import ImportFileError, apply_import, plan_import, read_table
from apps.users.models import StudentProfile, TeacherProfile
from apps.users.permissions import IsAdmin
from apps.users.serializers import (
    NewAccountSerializer, NewPasswordSerializer, StudentUpdateSerializer, TeacherUpdateSerializer,
    password_errors, pending_user_row, student_row, teacher_row,
)
from apps.users.services import blacklist_refresh_tokens, deactivate_user, restore_user
from config.errors import error_response, validation_error_response

User = get_user_model()

SIGN_UP_ROLES = (User.Role.STUDENT, User.Role.TEACHER)


def _deleted_filter(request):
    """?status=active (default) or deleted -> the `deleted` value to filter on; None if invalid."""
    value = (request.query_params.get('status') or 'active').strip().lower()
    return {'active': False, 'deleted': True}.get(value)


def _invalid_status():
    return error_response('Status must be "active" or "deleted".', 400, code='invalid_status')


def _search(queryset, text, *extra_fields):
    """Every word must match the name, the email or one of the extra fields."""
    for word in text.split():
        match = Q(user__first_name__icontains=word) | Q(user__last_name__icontains=word) | Q(user__email__icontains=word)
        for field in extra_fields:
            match |= Q(**{f'{field}__icontains': word})
        queryset = queryset.filter(match)
    return queryset


def _save(serializer, again):
    """Saves; if a unique value was taken meanwhile, answers with the same field errors as the check."""
    try:
        serializer.save()
        return None
    except IntegrityError:
        retry = again()
        if not retry.is_valid():
            return validation_error_response(retry.errors)
        return error_response('This could not be saved. Please try again.', 400)


# ── Approvals ─────────────────────────────────────────────────────────────────

class PendingUsersView(APIView):
    """Sign-ups waiting for approval, oldest first."""
    permission_classes = [IsAdmin]

    def get(self, request):
        users = (
            User.objects.filter(is_verified=False, deleted=False, role__in=SIGN_UP_ROLES)
            .select_related('student_profile', 'teacher_profile')
            .order_by('date_joined')
        )
        return Response({'success': True, 'users': [pending_user_row(u) for u in users]})


class VerifyUserView(APIView):
    """Approve a sign-up (POST; PATCH kept for the old app)."""
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        user = get_object_or_404(User, id=uuid, deleted=False)
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=['is_verified'])
        return Response({'success': True, 'message': f'{user.email} approved.'})

    patch = post


class RejectUserView(APIView):
    """Soft-deletes a sign-up; its email and ids stay taken."""
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        user = get_object_or_404(User, id=uuid, deleted=False, role__in=SIGN_UP_ROLES)
        deactivate_user(user)
        StudentFace.objects.filter(student__user=user).delete()  # biometric data goes with the account
        return Response({'success': True, 'message': f'{user.email} rejected.'})


class ResetUserPasswordView(APIView):
    """The admin sets a temporary password; the user's other sign-ins end."""
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        user = get_object_or_404(User, id=uuid, deleted=False)
        serializer = NewPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        password = serializer.validated_data['new_password']
        problems = password_errors(password, user)
        if problems:
            return validation_error_response({'new_password': problems})
        user.set_password(password)
        user.save(update_fields=['password'])
        blacklist_refresh_tokens(user)
        return Response({
            'success': True,
            'message': f'Password changed for {user.email}. They can sign in with it and change it.',
        })


# ── Students ──────────────────────────────────────────────────────────────────

def _students():
    return StudentProfile.objects.select_related('user').annotate(
        face_registered=Exists(StudentFace.objects.filter(student=OuterRef('pk'))),
    )


def _current_semesters(profiles) -> dict:
    """{profile id: the active semester whose class group they are in}."""
    memberships = (
        StudentClassroom.objects.filter(
            student__in=profiles,
            classroom__deleted=False,
            classroom__semester__is_active=True,
            classroom__semester__deleted=False,
        )
        .select_related('classroom__semester')
        .order_by('classroom__semester__level', 'classroom__semester__semester')
    )
    result = {}
    for membership in memberships:
        result.setdefault(membership.student_id, membership.classroom.semester)
    return result


def _student_payload(profile_id):
    profile = _students().get(id=profile_id)
    return student_row(profile, _current_semesters([profile.id]).get(profile.id), profile.face_registered)


class AdminStudentListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        deleted = _deleted_filter(request)
        if deleted is None:
            return _invalid_status()
        profiles = _students().filter(user__deleted=deleted).order_by('student_id')
        search = (request.query_params.get('search') or '').strip()
        if search:
            profiles = _search(profiles.annotate(student_id_text=Cast('student_id', CharField())),
                               search, 'student_id_text')
        level = (request.query_params.get('level') or '').strip()
        if level:
            profiles = profiles.filter(current_level__iexact=level)
        term = (request.query_params.get('semester') or '').strip()
        if term:
            profiles = profiles.filter(current_semester__iexact=term)

        profiles = list(profiles)
        semesters = _current_semesters([p.id for p in profiles]) if profiles else {}
        return Response({
            'success': True,
            'students': [student_row(p, semesters.get(p.id), p.face_registered) for p in profiles],
        })

    def post(self, request):
        """An approved student account; `password` is a temporary password."""
        data = request.data.copy()
        data['role'] = User.Role.STUDENT
        serializer = NewAccountSerializer(data=data, verified=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        problem = _save(serializer, lambda: NewAccountSerializer(data=data, verified=True))
        if problem:
            return problem
        profile_id = serializer.instance.student_profile.id
        return Response({'success': True, 'student': _student_payload(profile_id)}, status=status.HTTP_201_CREATED)


class AdminStudentDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        get_object_or_404(StudentProfile, id=uuid)
        return Response({'success': True, 'student': _student_payload(uuid)})

    def patch(self, request, uuid):
        """Partial: any of email, first_name, last_name, student_id, current_level, current_semester."""
        profile = get_object_or_404(StudentProfile.objects.select_related('user'), id=uuid, user__deleted=False)
        serializer = StudentUpdateSerializer(profile, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        problem = _save(serializer, lambda: StudentUpdateSerializer(
            StudentProfile.objects.select_related('user').get(id=uuid), data=request.data, partial=True))
        if problem:
            return problem
        return Response({'success': True, 'student': _student_payload(uuid)})

    put = patch  # the old app sends PUT

    def delete(self, request, uuid):
        """Soft delete; the student's face data is removed."""
        profile = get_object_or_404(StudentProfile.objects.select_related('user'), id=uuid, user__deleted=False)
        deactivate_user(profile.user)
        profile.faces.all().delete()  # biometric data goes with the account
        return Response({'success': True, 'message': 'Student deleted.'})


class AdminStudentRestoreView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        profile = get_object_or_404(StudentProfile.objects.select_related('user'), id=uuid)
        restore_user(profile.user)
        return Response({'success': True, 'message': 'Student restored.', 'student': _student_payload(uuid)})


class AdminStudentImportView(APIView):
    """Students from a .csv/.xlsx file. Dry run unless apply is "true"."""
    permission_classes = [IsAdmin]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get('file')
        if upload is None:
            return validation_error_response({'file': ['Choose a .csv or .xlsx file.']})
        apply = str(request.data.get('apply', '')).strip().lower() == 'true'

        semester = None
        semester_id = str(request.data.get('semester_id') or '').strip()
        if semester_id:
            try:
                semester = Semester.objects.get(id=uuid_lib.UUID(semester_id), deleted=False)
            except (ValueError, Semester.DoesNotExist):
                return validation_error_response({'semester_id': ['This semester was not found.']})

        try:
            rows, to_create = plan_import(read_table(upload))
        except ImportFileError as e:
            return error_response(e.message, 400, code='invalid_file')

        created = []
        if apply and to_create:
            try:
                created = apply_import(to_create, semester)
            except IntegrityError:
                return error_response(
                    'Some of these students were added meanwhile. Check the file again.', 409, code='conflict'
                )

        counts = Counter(row['status'] for row in rows)
        body = {
            'success': True,
            'applied': apply,
            'summary': {'create': counts['create'], 'exists': counts['exists'], 'error': counts['error']},
            'rows': rows,
        }
        if apply:
            body['created'] = created
        return Response(body)


# ── Teachers ──────────────────────────────────────────────────────────────────

def _teachers():
    return TeacherProfile.objects.select_related('user').annotate(
        course_count=Count(
            'course_infos',
            filter=Q(course_infos__deleted=False, course_infos__semester__deleted=False,
                     course_infos__course__deleted=False),
            distinct=True,
        ),
    )


def _teacher_payload(profile_id):
    profile = _teachers().get(id=profile_id)
    return teacher_row(profile, profile.course_count)


class AdminTeacherListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        deleted = _deleted_filter(request)
        if deleted is None:
            return _invalid_status()
        profiles = _teachers().filter(user__deleted=deleted).order_by('user__first_name', 'user__last_name')
        search = (request.query_params.get('search') or '').strip()
        if search:
            profiles = _search(profiles, search, 'employee_id')
        return Response({'success': True, 'teachers': [teacher_row(p, p.course_count) for p in profiles]})

    def post(self, request):
        """An approved teacher account; `password` is a temporary password."""
        data = request.data.copy()
        data['role'] = User.Role.TEACHER
        serializer = NewAccountSerializer(data=data, verified=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        problem = _save(serializer, lambda: NewAccountSerializer(data=data, verified=True))
        if problem:
            return problem
        profile_id = serializer.instance.teacher_profile.id
        return Response({'success': True, 'teacher': _teacher_payload(profile_id)}, status=status.HTTP_201_CREATED)


class AdminTeacherDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        get_object_or_404(TeacherProfile, id=uuid)
        return Response({'success': True, 'teacher': _teacher_payload(uuid)})

    def patch(self, request, uuid):
        """Partial: any of email, first_name, last_name, employee_id."""
        profile = get_object_or_404(TeacherProfile.objects.select_related('user'), id=uuid, user__deleted=False)
        serializer = TeacherUpdateSerializer(profile, data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        problem = _save(serializer, lambda: TeacherUpdateSerializer(
            TeacherProfile.objects.select_related('user').get(id=uuid), data=request.data, partial=True))
        if problem:
            return problem
        return Response({'success': True, 'teacher': _teacher_payload(uuid)})

    put = patch  # the old app sends PUT

    def delete(self, request, uuid):
        profile = get_object_or_404(TeacherProfile.objects.select_related('user'), id=uuid, user__deleted=False)
        deactivate_user(profile.user)
        return Response({'success': True, 'message': 'Teacher deleted.'})


class AdminTeacherRestoreView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, uuid):
        profile = get_object_or_404(TeacherProfile.objects.select_related('user'), id=uuid)
        restore_user(profile.user)
        return Response({'success': True, 'message': 'Teacher restored.', 'teacher': _teacher_payload(uuid)})
