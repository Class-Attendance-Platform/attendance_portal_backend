from rest_framework import serializers
from .models import Semester, Course, Classroom, StudentClassroom, CourseInfo
from apps.users.models import StudentProfile, TeacherProfile


class SemesterSerializer(serializers.ModelSerializer):
    students = serializers.ListField(child=serializers.UUIDField(), required=False, write_only=False)
    courses = serializers.ListField(child=serializers.UUIDField(), required=False, write_only=False)

    class Meta:
        model = Semester
        fields = ["id", "level", "semester", "start_date", "end_date", "is_active", "students", "courses"]

    def get_students(self, obj):
        from apps.academic.models import StudentClassroom
        student_ids = StudentClassroom.objects.filter(classroom__semester=obj).values_list("student_id", flat=True).distinct()
        return [str(sid) for sid in student_ids]

    def get_courses(self, obj):
        return [str(ci.id) for ci in obj.course_infos.all()]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["students"] = self.get_students(instance)
        ret["courses"] = self.get_courses(instance)
        return ret

    def create(self, validated_data):
        student_ids = validated_data.pop("students", [])
        course_info_ids = validated_data.pop("courses", [])
        semester = Semester.objects.create(**validated_data)
        self._sync_relations(semester, student_ids, course_info_ids)
        return semester

    def update(self, instance, validated_data):
        student_ids = validated_data.pop("students", None)
        course_info_ids = validated_data.pop("courses", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if student_ids is not None or course_info_ids is not None:
            self._sync_relations(instance, student_ids, course_info_ids)
        return instance

    def _sync_relations(self, semester, student_ids, course_info_ids):
        from apps.academic.models import Classroom, StudentClassroom, CourseInfo
        from apps.users.models import StudentProfile
        
        # 1. Sync Students (via a default Classroom)
        if student_ids is not None:
            classroom, _ = Classroom.objects.get_or_create(
                semester=semester, 
                name="Main",
                defaults={"deleted": False}
            )
            # Remove students not in the list
            StudentClassroom.objects.filter(classroom=classroom).exclude(student_id__in=student_ids).delete()
            # Add new students
            for sid in student_ids:
                try:
                    student = StudentProfile.objects.get(id=sid)
                    StudentClassroom.objects.get_or_create(student=student, classroom=classroom)
                except StudentProfile.DoesNotExist:
                    continue

        # 2. Sync CourseInfos
        if course_info_ids is not None:
            # First, CourseInfos that were pointing here but are no longer in the list:
            # We dont delete CourseInfos, we just unbind them or leave them? 
            # Actually, CourseInfo is tied to Semester. If it is removed from Semester, it should probably be deleted or moved.
            # But the UI suggests we are selecting existing CourseInfos.
            # Lets just update the semester FK for selected CourseInfos.
            CourseInfo.objects.filter(id__in=course_info_ids).update(semester=semester)



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
