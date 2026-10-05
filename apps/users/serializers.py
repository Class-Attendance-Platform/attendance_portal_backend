from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers

from .constants import DEPARTMENT, EMAIL_MAX_LENGTH, FACULTY, LEVELS, TERMS
from .models import DeviceBinding, StudentProfile, TeacherProfile
from .services import iso

User = get_user_model()


# ── What the API sends ────────────────────────────────────────────────────────


def user_data(user) -> dict:
    """The signed-in user (login and /auth/me/)."""
    student_profile = None
    if user.role == User.Role.STUDENT and hasattr(user, 'student_profile'):
        p = user.student_profile
        student_profile = {
            'id': str(p.id),
            'student_id': p.student_id,
            'current_level': p.current_level,
            'current_semester': p.current_semester,
        }
    teacher_profile = None
    if user.role == User.Role.TEACHER and hasattr(user, 'teacher_profile'):
        p = user.teacher_profile
        teacher_profile = {'id': str(p.id), 'employee_id': p.employee_id}
    return {
        'id': str(user.id),
        'userName': user.get_full_name() or user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'role': user.role,
        'is_verified': user.is_verified,
        'date_joined': iso(user.date_joined),
        'student_profile': student_profile,
        'teacher_profile': teacher_profile,
    }


def _account_fields(user) -> dict:
    return {
        'user_id': str(user.id),
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'userName': user.get_full_name() or user.username,
    }


def _status_fields(user) -> dict:
    return {
        'is_verified': user.is_verified,
        'is_active': user.is_active,
        'deleted': user.deleted,
        'last_login': iso(user.last_login),
    }


def student_row(profile, semester=None, face_registered=False) -> dict:
    """Admin students list row. `semester`: the active semester they are in, if any."""
    user = profile.user
    return {
        'id': str(profile.id),
        **_account_fields(user),
        'student_id': profile.student_id,
        'current_level': profile.current_level,
        'current_semester': profile.current_semester,
        **_status_fields(user),
        'face_registered': bool(face_registered),
        'semester': {'id': str(semester.id), 'label': semester.label} if semester else None,
    }


def teacher_row(profile, course_count=0) -> dict:
    user = profile.user
    return {
        'id': str(profile.id),
        **_account_fields(user),
        'employee_id': profile.employee_id,
        **_status_fields(user),
        'course_count': course_count,
    }


def pending_user_row(user) -> dict:
    row = {
        'id': str(user.id),
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': user.role,
        'date_joined': iso(user.date_joined),
    }
    if hasattr(user, 'student_profile'):
        p = user.student_profile
        row.update(student_id=p.student_id, current_level=p.current_level, current_semester=p.current_semester)
    if hasattr(user, 'teacher_profile'):
        row['employee_id'] = user.teacher_profile.employee_id
    return row


# ── Uniqueness (emails ignore capitals; the username is the email) ───────────


def email_taken(email, exclude_user=None) -> bool:
    qs = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
    if exclude_user is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.exists()


def student_id_taken(student_id, exclude_profile=None) -> bool:
    qs = StudentProfile.objects.filter(student_id=student_id)
    if exclude_profile is not None:
        qs = qs.exclude(pk=exclude_profile.pk)
    return qs.exists()


def employee_id_taken(employee_id, exclude_profile=None) -> bool:
    qs = TeacherProfile.objects.filter(employee_id__iexact=employee_id)
    if exclude_profile is not None:
        qs = qs.exclude(pk=exclude_profile.pk)
    return qs.exists()


LEVEL_ERRORS = {'invalid_choice': 'Level must be First, Second, Third or Fourth.'}
TERM_ERRORS = {'invalid_choice': 'Term must be I or II.'}

EMAIL_TAKEN = 'An account with this email already exists.'
STUDENT_ID_TAKEN = 'A student with this student ID already exists.'
EMPLOYEE_ID_TAKEN = 'A teacher with this employee ID already exists.'


def password_errors(password, user):
    """Django's password rules (8+ characters, not common, not all digits, not like the name)."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as e:
        return list(e.messages)
    return []


# ── Auth ──────────────────────────────────────────────────────────────────────


class LoginSerializer(serializers.Serializer):
    # Not an EmailField: a typo should read "Email or password is incorrect."
    email = serializers.CharField(max_length=254)
    password = serializers.CharField(trim_whitespace=False, max_length=128)


class NewAccountSerializer(serializers.Serializer):
    """
    A student or teacher account with its profile, made in one transaction.
    Sign-up makes it waiting for approval; admins create approved accounts (verified=True).
    """
    role = serializers.CharField()
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    # Students
    student_id = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    current_level = serializers.ChoiceField(choices=LEVELS, required=False, error_messages=LEVEL_ERRORS)
    current_semester = serializers.ChoiceField(choices=TERMS, required=False, error_messages=TERM_ERRORS)
    # Teachers
    employee_id = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def __init__(self, *args, verified=False, **kwargs):
        self.verified = verified
        super().__init__(*args, **kwargs)

    def validate_role(self, value):
        role = str(value).strip().upper()
        if role == User.Role.ADMIN:
            raise serializers.ValidationError('Admin accounts cannot be created by sign-up.')
        if role not in (User.Role.STUDENT, User.Role.TEACHER):
            raise serializers.ValidationError('Choose Student or Teacher.')
        return role

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        errors = {}
        role = attrs['role']
        if role == User.Role.STUDENT:
            for name, label in (('student_id', 'Student ID'), ('current_level', 'Level'), ('current_semester', 'Term')):
                if attrs.get(name) in (None, ''):
                    errors[name] = [f'{label} is required for students.']
            if attrs.get('student_id') and student_id_taken(attrs['student_id']):
                errors['student_id'] = [STUDENT_ID_TAKEN]
        else:
            attrs['employee_id'] = (attrs.get('employee_id') or '').strip()
            if not attrs['employee_id']:
                errors['employee_id'] = ['Employee ID is required for teachers.']
            elif employee_id_taken(attrs['employee_id']):
                errors['employee_id'] = [EMPLOYEE_ID_TAKEN]

        if email_taken(attrs['email']):
            errors['email'] = [EMAIL_TAKEN]

        candidate = User(
            username=attrs['email'], email=attrs['email'],
            first_name=attrs['first_name'], last_name=attrs.get('last_name', ''),
        )
        problems = password_errors(attrs['password'], candidate)
        if problems:
            errors['password'] = problems

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        email = validated_data['email']
        with transaction.atomic():
            user = User(
                username=email,
                email=email,
                first_name=validated_data['first_name'].strip(),
                last_name=validated_data.get('last_name', '').strip(),
                role=validated_data['role'],
                faculty=FACULTY,
                department=DEPARTMENT,
                is_verified=self.verified,
            )
            user.set_password(validated_data['password'])
            user.save()
            if user.role == User.Role.STUDENT:
                StudentProfile.objects.create(
                    user=user,
                    student_id=validated_data['student_id'],
                    current_level=validated_data['current_level'],
                    current_semester=validated_data['current_semester'],
                )
            else:
                TeacherProfile.objects.create(user=user, employee_id=validated_data['employee_id'])
        return user


class MeUpdateSerializer(serializers.Serializer):
    """Users may change only their name."""
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, max_length=128)
    new_password = serializers.CharField(trim_whitespace=False, max_length=128)


class PasswordForgotSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class PasswordResetSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=200)
    token = serializers.CharField(max_length=200)
    new_password = serializers.CharField(trim_whitespace=False, max_length=128)


class NewPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(trim_whitespace=False, max_length=128)


# ── Admin: partial updates ────────────────────────────────────────────────────


class _ProfileUpdateSerializer(serializers.Serializer):
    """PATCH: any subset of the fields; the instance is a Student/TeacherProfile."""
    email = serializers.EmailField(max_length=EMAIL_MAX_LENGTH, required=False)
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    USER_FIELDS = ('email', 'first_name', 'last_name')

    def validate_email(self, value):
        value = value.strip().lower()
        if email_taken(value, exclude_user=self.instance.user):
            raise serializers.ValidationError(EMAIL_TAKEN)
        return value

    def update(self, instance, validated_data):
        user = instance.user
        with transaction.atomic():
            user_changed = []
            for name in self.USER_FIELDS:
                if name in validated_data:
                    setattr(user, name, validated_data[name].strip())
                    user_changed.append(name)
            if 'email' in validated_data:
                user.username = user.email
                user_changed.append('username')
            if user_changed:
                user.save(update_fields=user_changed)
            profile_changed = [name for name in validated_data if name not in self.USER_FIELDS]
            for name in profile_changed:
                setattr(instance, name, validated_data[name])
            if profile_changed:
                instance.save(update_fields=profile_changed)
        return instance


class StudentUpdateSerializer(_ProfileUpdateSerializer):
    student_id = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    current_level = serializers.ChoiceField(choices=LEVELS, required=False, error_messages=LEVEL_ERRORS)
    current_semester = serializers.ChoiceField(choices=TERMS, required=False, error_messages=TERM_ERRORS)

    def validate_student_id(self, value):
        if student_id_taken(value, exclude_profile=self.instance):
            raise serializers.ValidationError(STUDENT_ID_TAKEN)
        return value


class TeacherUpdateSerializer(_ProfileUpdateSerializer):
    employee_id = serializers.CharField(max_length=50, required=False)

    def validate_employee_id(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Employee ID is required.')
        if employee_id_taken(value, exclude_profile=self.instance):
            raise serializers.ValidationError(EMPLOYEE_ID_TAKEN)
        return value


# ── Device Binding ────────────────────────────────────────────────────────────


class DeviceBindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceBinding
        fields = ["id", "mac_address", "bound_at", "is_active"]
