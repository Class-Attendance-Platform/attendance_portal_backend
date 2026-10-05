import re

from rest_framework import serializers

from .models import Semester, Course, Classroom, StudentClassroom, CourseInfo
from apps.users.constants import LEVELS, TERMS
from apps.users.models import StudentProfile


def iso_date(day):
    return day.isoformat() if day else None


def person_name(user) -> str:
    return user.get_full_name() or user.username


# ── What the API sends (API v2 section 3) ─────────────────────────────────────


def semester_row(semester, classroom=None, student_count=0, course_count=0) -> dict:
    return {
        'id': str(semester.id),
        'level': semester.level,
        'semester': semester.semester,
        'session': semester.session,
        'label': semester.label,
        'start_date': iso_date(semester.start_date),
        'end_date': iso_date(semester.end_date),
        'is_active': semester.is_active,
        'deleted': semester.deleted,
        'classroom_id': str(classroom.id) if classroom else None,
        'student_count': student_count,
        'course_count': course_count,
    }


def semester_brief(semester) -> dict:
    return {'id': str(semester.id), 'label': semester.label, 'is_active': semester.is_active}


def roster_row(membership) -> dict:
    student = membership.student
    return {
        'profile_id': str(student.id),
        'student_id': student.student_id,
        'name': person_name(student.user),
        'email': student.user.email,
        'joined_at': iso_date(membership.joined_at),
        'left_at': iso_date(membership.left_at),
    }


def teacher_brief(teacher):
    """{id, name, deleted}: `deleted` = the teacher's account was deleted (choose another teacher)."""
    if not teacher:
        return None
    return {'id': str(teacher.id), 'name': person_name(teacher.user), 'deleted': teacher.user.deleted}


def semester_course_row(ci) -> dict:
    """A course taught in a semester (GET /admin/semesters/<id>/courses/)."""
    return {
        'course_info_id': str(ci.id),
        'course': {
            'id': str(ci.course.id),
            'code': ci.course.code,
            'title': ci.course.title,
            'credits': ci.course.credits,
        },
        'teacher': teacher_brief(ci.teacher),
    }


def admin_course_info_row(ci, numbers) -> dict:
    """GET /admin/course-info/ row; `numbers` from apps.academic.stats.course_numbers."""
    return {
        'id': str(ci.id),
        'course': {'id': str(ci.course.id), 'code': ci.course.code, 'title': ci.course.title},
        'teacher': teacher_brief(ci.teacher),
        'semester': semester_brief(ci.semester),
        'student_count': numbers.student_count,
        'classes_held': numbers.classes_held,
        'average_percent': numbers.average_percent,
    }


# ── What the API reads ────────────────────────────────────────────────────────

SESSION_PATTERN = re.compile(r'^(\d{4})\s*[-/–]\s*(\d{2}|\d{4})$')
SESSION_MESSAGE = 'Session must look like 2025-26.'


def normalize_session(text) -> str:
    """"2025-26", "2025-2026" or "2025/26" -> "2025-26"."""
    match = SESSION_PATTERN.match(str(text).strip())
    if not match:
        raise serializers.ValidationError(SESSION_MESSAGE)
    first, second = int(match[1]), match[2]
    expected = first + 1
    if (len(second) == 4 and int(second) != expected) or (len(second) == 2 and int(second) != expected % 100):
        raise serializers.ValidationError(SESSION_MESSAGE)
    return f'{first}-{expected % 100:02d}'


def _check_dates(start, end):
    if start and end and end < start:
        raise serializers.ValidationError({'end_date': ['End date must be on or after the start date.']})


class SemesterCreateSerializer(serializers.Serializer):
    level = serializers.ChoiceField(
        choices=LEVELS, error_messages={'invalid_choice': 'Level must be First, Second, Third or Fourth.'},
    )
    semester = serializers.ChoiceField(  # the term
        choices=TERMS,
        error_messages={'invalid_choice': 'Term must be I or II.', 'required': 'Term is required.',
                        'null': 'Term is required.'},
    )
    session = serializers.CharField(max_length=20)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)

    def validate_session(self, value):
        return normalize_session(value)

    def validate(self, data):
        _check_dates(data.get('start_date'), data.get('end_date'))
        return data


class SemesterUpdateSerializer(serializers.Serializer):
    """PATCH: session, start and end date only (level and term never change)."""
    session = serializers.CharField(max_length=20, required=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)

    def validate_session(self, value):
        return normalize_session(value)

    def validate(self, data):
        start = data['start_date'] if 'start_date' in data else self.instance.start_date
        end = data['end_date'] if 'end_date' in data else self.instance.end_date
        _check_dates(start, end)
        return data

    def update(self, instance, validated_data):
        for name, value in validated_data.items():
            setattr(instance, name, value)
        instance.save(update_fields=list(validated_data) or None)
        return instance


class ProfileIdsSerializer(serializers.Serializer):
    profile_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=2000)


class SemesterCourseSerializer(serializers.Serializer):
    course_id = serializers.UUIDField()
    teacher_id = serializers.UUIDField(required=False, allow_null=True)


class CourseInfoTeacherSerializer(serializers.Serializer):
    teacher_id = serializers.UUIDField(allow_null=True)


class PromoteSerializer(serializers.Serializer):
    target = SemesterCreateSerializer(required=False, allow_null=True)
    target_semester_id = serializers.UUIDField(required=False, allow_null=True)
    profile_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=2000,
    )

    def validate(self, data):
        has_target, has_id = bool(data.get('target')), bool(data.get('target_semester_id'))
        if has_target == has_id:
            raise serializers.ValidationError(
                'Choose the next semester: either an existing one (target_semester_id) or a new one (target).'
            )
        return data


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'code', 'title', 'content', 'credits', 'faculty', 'department', 'deleted']
        read_only_fields = ['deleted']
        extra_kwargs = {'code': {'validators': []}}  # checked in validate_code with a clearer message

    def validate_code(self, value):
        code = value.strip()
        if not code:
            raise serializers.ValidationError('Code is required.')
        clash = Course.objects.filter(code__iexact=code)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        clash = clash.first()
        if clash is not None:
            hint = ' It is deleted: restore it instead.' if clash.deleted else ''
            raise serializers.ValidationError(f'A course with the code {clash.code} already exists.{hint}')
        return code


# ── Older endpoints (classrooms, course-info POST); the redesigned app does not use them ──


class SemesterSerializer(serializers.ModelSerializer):
    """Read-only semester summary inside the older classroom / course-info answers."""
    students = serializers.SerializerMethodField()
    courses = serializers.SerializerMethodField()

    class Meta:
        model = Semester
        fields = ["id", "level", "semester", "session", "start_date", "end_date", "is_active", "students", "courses"]
        read_only_fields = fields

    def get_students(self, obj):
        student_ids = (
            StudentClassroom.objects.filter(classroom__semester=obj).current()
            .values_list("student_id", flat=True).distinct()
        )
        return [str(sid) for sid in student_ids]

    def get_courses(self, obj):
        return [str(ci.id) for ci in obj.course_infos.filter(deleted=False)]


class ClassroomSerializer(serializers.ModelSerializer):
    semester_detail = SemesterSerializer(source='semester', read_only=True)
    student_count = serializers.SerializerMethodField()

    class Meta:
        model = Classroom
        fields = ['id', 'name', 'semester', 'semester_detail', 'student_count']

    def get_student_count(self, obj):
        return obj.memberships.current().active_accounts().count()


class StudentInClassroomSerializer(serializers.ModelSerializer):
    """Minimal student info for classroom roster."""
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    userName = serializers.SerializerMethodField()

    class Meta:
        model = StudentProfile
        fields = ['id', 'user_id', 'first_name', 'last_name', 'email', 'student_id', 'current_level', 'current_semester', 'userName']

    def get_userName(self, obj):
        return obj.user.get_full_name() or obj.user.username


class ClassroomStudentBulkSerializer(serializers.Serializer):
    """For add/remove students from classroom."""
    student_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1
    )


class CourseInfoSerializer(serializers.ModelSerializer):
    course_detail = CourseSerializer(source='course', read_only=True)
    teacher_detail = serializers.SerializerMethodField()
    semester_detail = SemesterSerializer(source='semester', read_only=True)
    classroom_detail = ClassroomSerializer(source='classroom', read_only=True)

    class Meta:
        model = CourseInfo
        fields = [
            'id', 'course', 'teacher', 'semester', 'classroom',
            'course_detail', 'teacher_detail', 'semester_detail', 'classroom_detail',
        ]

    def get_teacher_detail(self, obj):
        if not obj.teacher:
            return None
        return {
            'id': str(obj.teacher.id),
            'user_id': str(obj.teacher.user.id),
            'name': obj.teacher.user.get_full_name(),
            'email': obj.teacher.user.email,
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['course'] = CourseSerializer(instance.course).data if instance.course else None
        if instance.teacher:
            ret['teacher'] = {
                'id': str(instance.teacher.id),
                'userName': instance.teacher.user.get_full_name(),
                'email': instance.teacher.user.email,
            }
        else:
            ret['teacher'] = None
        return ret


class PromoteStudentsSerializer(serializers.Serializer):
    new_level = serializers.CharField()
    new_semester = serializers.CharField()
    student_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text='If omitted, promotes ALL students in the classroom.'
    )
