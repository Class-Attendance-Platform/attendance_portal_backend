from rest_framework import serializers
from .models import Semester, Course, Classroom, StudentClassroom, CourseInfo
from apps.users.models import StudentProfile, TeacherProfile


class SemesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Semester
        fields = ['id', 'level', 'semester', 'start_date', 'end_date', 'is_active']

    def validate(self, attrs):
        if attrs.get('start_date') and attrs.get('end_date'):
            if attrs['start_date'] > attrs['end_date']:
                raise serializers.ValidationError('start_date must be before end_date.')
        return attrs


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'code', 'title', 'content', 'credits', 'faculty', 'department']


class ClassroomSerializer(serializers.ModelSerializer):
    semester_detail = SemesterSerializer(source='semester', read_only=True)
    student_count = serializers.SerializerMethodField()

    class Meta:
        model = Classroom
        fields = ['id', 'name', 'semester', 'semester_detail', 'student_count']

    def get_student_count(self, obj):
        return obj.memberships.count()


class StudentInClassroomSerializer(serializers.ModelSerializer):
    """Minimal student info for classroom roster."""
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = StudentProfile
        fields = ['id', 'user_id', 'first_name', 'last_name', 'email', 'student_id', 'current_level', 'current_semester']


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


class PromoteStudentsSerializer(serializers.Serializer):
    new_level = serializers.CharField()
    new_semester = serializers.CharField()
    student_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text='If omitted, promotes ALL students in the classroom.'
    )
