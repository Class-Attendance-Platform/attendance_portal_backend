from rest_framework import serializers
from .models import Semester, Course, Classroom, StudentClassroom, CourseInfo
from apps.users.models import StudentProfile, TeacherProfile


class SemesterSerializer(serializers.ModelSerializer):
    students = serializers.ListField(child=serializers.UUIDField(), required=False, write_only=False)
    courses = serializers.ListField(required=False, write_only=False)

    class Meta:
        model = Semester
        fields = ["id", "level", "semester", "start_date", "end_date", "is_active", "students", "courses"]

    def get_students(self, obj):
        from apps.academic.models import StudentClassroom
        student_ids = StudentClassroom.objects.filter(classroom__semester=obj).values_list("student_id", flat=True).distinct()
        return [str(sid) for sid in student_ids]

    def get_courses(self, obj):
        return [str(ci.id) for ci in obj.course_infos.filter(deleted=False)]

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
            classroom, _ = Classroom.objects.get_or_create(
                semester=semester, 
                name="Main",
                defaults={"deleted": False}
            )
            
            # Parse the incoming courses data (can be Course IDs or dicts of {course_id, teacher_id})
            parsed_courses = []
            for item in course_info_ids:
                if isinstance(item, dict):
                    cid = item.get('course_id') or item.get('course')
                    tid = item.get('teacher_id') or item.get('teacher')
                else:
                    cid = item
                    tid = None
                if cid:
                    parsed_courses.append((str(cid), str(tid) if tid else None))
            
            selected_course_ids = [c[0] for c in parsed_courses]
            
            # Soft-delete CourseInfos that were associated with this semester but are no longer in the list
            CourseInfo.objects.filter(semester=semester).exclude(course_id__in=selected_course_ids).update(deleted=True)
            # Update and restore/create selected CourseInfos
            for cid, tid in parsed_courses:
                ci = CourseInfo.objects.filter(semester=semester, course_id=cid).first()
                if ci:
                    ci.classroom = classroom
                    ci.teacher_id = tid
                    ci.deleted = False
                    ci.save()
                else:
                    CourseInfo.objects.create(
                        semester=semester,
                        course_id=cid,
                        teacher_id=tid,
                        classroom=classroom,
                        deleted=False
                    )



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
