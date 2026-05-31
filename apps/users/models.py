import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = 'STUDENT', 'Student'
        TEACHER = 'TEACHER', 'Teacher'
        ADMIN = 'ADMIN', 'Admin'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices)
    faculty = models.CharField(max_length=100, blank=True, default='')
    department = models.CharField(max_length=100, blank=True, default='')
    is_verified = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'role']

    class Meta:
        ordering = ['-date_joined']

    def __str__(self):
        return f'{self.email} ({self.role})'


class StudentProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    student_id = models.IntegerField(unique=True)          # e.g. 2302001
    current_level = models.CharField(max_length=20)        # e.g. "Third"
    current_semester = models.CharField(max_length=5)      # e.g. "I"
    hardware_finger_id = models.IntegerField(null=True, blank=True, unique=True)

    def __str__(self):
        return f'{self.user.get_full_name()} ({self.student_id})'


class TeacherProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    employee_id = models.CharField(max_length=50, unique=True, blank=True, default='')

    def __str__(self):
        return f'{self.user.get_full_name()}'


class AdminProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_profile')

    def __str__(self):
        return f'{self.user.get_full_name()}'


class DeviceBinding(models.Model):
    """Binds a student to a specific device MAC address for QR attendance."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.OneToOneField(StudentProfile, on_delete=models.CASCADE, related_name='device_binding')
    mac_address = models.CharField(max_length=17, unique=True)  # e.g. AA:BB:CC:DD:EE:FF
    bound_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.student} → {self.mac_address}'
