from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import AdminProfile, DeviceBinding, StudentProfile, TeacherProfile

User = get_user_model()


# ── Auth ──────────────────────────────────────────────────────────────────────


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    # Student-only fields
    student_id = serializers.IntegerField(required=False)
    current_level = serializers.CharField(required=False)
    current_semester = serializers.CharField(required=False)
    # Teacher-only fields
    employee_id = serializers.CharField(required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "password",
            "first_name",
            "last_name",
            "role",
            "faculty",
            "department",
            # student extras
            "student_id",
            "current_level",
            "current_semester",
            # teacher extras
            "employee_id",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("email"):
            email = attrs["email"].lower().strip()
            attrs["email"] = email
            attrs["username"] = email

        role = attrs.get("role")
        if role == User.Role.STUDENT:
            if not attrs.get("student_id"):
                raise serializers.ValidationError(
                    {"student_id": "Required for students."}
                )
            if not attrs.get("current_level"):
                raise serializers.ValidationError(
                    {"current_level": "Required for students."}
                )
            if not attrs.get("current_semester"):
                raise serializers.ValidationError(
                    {"current_semester": "Required for students."}
                )
        elif role == User.Role.TEACHER:
            if not attrs.get("employee_id"):
                raise serializers.ValidationError(
                    {"employee_id": "Required for teachers."}
                )
        return attrs

    def create(self, validated_data):
        # Pop profile-specific fields
        student_id = validated_data.pop("student_id", None)
        current_level = validated_data.pop("current_level", None)
        current_semester = validated_data.pop("current_semester", None)
        employee_id = validated_data.pop("employee_id", "")
        password = validated_data.pop("password")

        user = User(**validated_data)
        user.set_password(password)
        user.save()

        if user.role == User.Role.STUDENT:
            StudentProfile.objects.create(
                user=user,
                student_id=student_id,
                current_level=current_level,
                current_semester=current_semester,
            )
        elif user.role == User.Role.TEACHER:
            TeacherProfile.objects.create(user=user, employee_id=employee_id)
        elif user.role == User.Role.ADMIN:
            AdminProfile.objects.create(user=user)

        return user


class UserMeSerializer(serializers.ModelSerializer):
    userName = serializers.SerializerMethodField()
    student_profile = serializers.SerializerMethodField()
    teacher_profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "userName",
            "first_name",
            "last_name",
            "role",
            "faculty",
            "department",
            "is_verified",
            "student_profile",
            "teacher_profile",
        ]

    def get_userName(self, obj):
        return obj.get_full_name() or obj.username

    def get_student_profile(self, obj):
        if obj.role == User.Role.STUDENT and hasattr(obj, "student_profile"):
            p = obj.student_profile
            return {
                "id": str(p.id),
                "student_id": p.student_id,
                "current_level": p.current_level,
                "current_semester": p.current_semester,
                "hardware_finger_id": p.hardware_finger_id,
            }
        return None

    def get_teacher_profile(self, obj):
        if obj.role == User.Role.TEACHER and hasattr(obj, "teacher_profile"):
            p = obj.teacher_profile
            return {"id": str(p.id), "employee_id": p.employee_id}
        return None


# ── Admin — Student ───────────────────────────────────────────────────────────


class StudentProfileSerializer(serializers.ModelSerializer):
    userName = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    faculty = serializers.CharField(source="user.faculty", read_only=True)
    department = serializers.CharField(source="user.department", read_only=True)
    is_verified = serializers.BooleanField(source="user.is_verified", read_only=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "user_id",
            "userName",
            "username",
            "first_name",
            "last_name",
            "email",
            "faculty",
            "department",
            "is_verified",
            "student_id",
            "current_level",
            "current_semester",
            "hardware_finger_id",
        ]

    def get_userName(self, obj):
        return obj.user.get_full_name() or obj.user.username


class StudentUpdateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source="user.first_name", required=False, allow_blank=True)
    last_name = serializers.CharField(source="user.last_name", required=False, allow_blank=True)
    faculty = serializers.CharField(source="user.faculty", required=False)
    department = serializers.CharField(source="user.department", required=False)

    class Meta:
        model = StudentProfile
        fields = [
            "first_name",
            "last_name",
            "faculty",
            "department",
            "current_level",
            "current_semester",
        ]

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        for attr, value in user_data.items():
            setattr(instance.user, attr, value)
        instance.user.save()
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ── Admin — Teacher ───────────────────────────────────────────────────────────


class TeacherProfileSerializer(serializers.ModelSerializer):
    userName = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    faculty = serializers.CharField(source="user.faculty", read_only=True)
    department = serializers.CharField(source="user.department", read_only=True)
    is_verified = serializers.BooleanField(source="user.is_verified", read_only=True)
    user_id = serializers.UUIDField(source="user.id", read_only=True)

    class Meta:
        model = TeacherProfile
        fields = [
            "id",
            "user_id",
            "userName",
            "username",
            "first_name",
            "last_name",
            "email",
            "faculty",
            "department",
            "is_verified",
            "employee_id",
        ]

    def get_userName(self, obj):
        return obj.user.get_full_name() or obj.user.username


class TeacherUpdateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source="user.first_name", required=False, allow_blank=True)
    last_name = serializers.CharField(source="user.last_name", required=False, allow_blank=True)
    faculty = serializers.CharField(source="user.faculty", required=False)
    department = serializers.CharField(source="user.department", required=False)

    class Meta:
        model = TeacherProfile
        fields = ["first_name", "last_name", "faculty", "department", "employee_id"]

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        for attr, value in user_data.items():
            setattr(instance.user, attr, value)
        instance.user.save()
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ── Device Binding ────────────────────────────────────────────────────────────


class DeviceBindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceBinding
        fields = ["id", "mac_address", "bound_at", "is_active"]
