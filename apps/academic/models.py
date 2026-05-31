import uuid
from django.db import models


class Semester(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    level = models.CharField(max_length=20)       # e.g. "Third"
    semester = models.CharField(max_length=5)     # e.g. "I"
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_active', 'level', 'semester']

    def __str__(self):
        return f'{self.level} - {self.semester}'


class Course(models.Model):
    class Credits(models.TextChoices):
        CREDIT_1_00 = 'CREDIT_1_00', '1.00'
        CREDIT_1_50 = 'CREDIT_1_50', '1.50'
        CREDIT_2_00 = 'CREDIT_2_00', '2.00'
        CREDIT_3_00 = 'CREDIT_3_00', '3.00'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True, default='')
    credits = models.CharField(max_length=15, choices=Credits.choices, default=Credits.CREDIT_3_00)
    faculty = models.CharField(max_length=100, default='COMPUTER_SCIENCE_AND_ENGINEERING')
    department = models.CharField(max_length=100, default='COMPUTER_SCIENCE_AND_ENGINEERING')
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.title}'


class Classroom(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='classrooms')
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.semester})'


class StudentClassroom(models.Model):
    student = models.ForeignKey(
        'users.StudentProfile',
        on_delete=models.CASCADE,
        related_name='classroom_memberships'
    )
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.CASCADE,
        related_name='memberships'
    )

    class Meta:
        unique_together = ('student', 'classroom')

    def __str__(self):
        return f'{self.student} in {self.classroom}'


class CourseInfo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='course_infos')
    teacher = models.ForeignKey(
        'users.TeacherProfile',
        on_delete=models.SET_NULL,
        null=True,
        related_name='course_infos'
    )
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE, related_name='course_infos')
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='course_infos')
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-semester__is_active', 'course__code']
        unique_together = ('course', 'teacher', 'semester', 'classroom')

    def __str__(self):
        return f'{self.course.code} / {self.teacher} / {self.semester}'
