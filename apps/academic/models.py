import uuid
from django.db import models
from django.db.models import Q

LEVEL_NUMBERS = {'First': 1, 'Second': 2, 'Third': 3, 'Fourth': 4}
TERM_NUMBERS = {'I': 1, 'II': 2}


class Semester(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    level = models.CharField(max_length=20)       # e.g. "Third"
    semester = models.CharField(max_length=5)     # e.g. "I"
    session = models.CharField(max_length=20, blank=True, default='')  # academic year, e.g. "2025-26"
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_active', 'level', 'semester']

    def __str__(self):
        return f'{self.level} - {self.semester}'

    @property
    def label(self):
        """What people read, e.g. "Level 3 · Term I · 2025-26" (the session part only when set)."""
        level = LEVEL_NUMBERS.get(self.level, self.level)
        parts = [f'Level {level}', f'Term {self.semester}']
        if self.session:
            parts.append(self.session)
        return ' · '.join(parts)

    def sort_key(self):
        """Level then term in teaching order (First..Fourth, I..II), unknown values last."""
        return LEVEL_NUMBERS.get(self.level, 99), TERM_NUMBERS.get(self.semester, 99)


def sort_semesters(semesters):
    """Active first, then by session (newest first, none last), level, term."""
    ordered = sorted(semesters, key=lambda s: s.sort_key())
    ordered.sort(key=lambda s: s.session, reverse=True)  # stable; a blank session sorts last
    ordered.sort(key=lambda s: not s.is_active)
    return ordered


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


class MembershipQuerySet(models.QuerySet):
    """
    A membership covers the dates joined_at <= day < left_at. joined_at null = from the
    start; left_at null = still a member. Former members keep their row (history). Numbers
    use StudentClassroom.counts_on (on the join day only classes that logged them count).
    """

    def current(self):
        return self.filter(left_at__isnull=True)

    def former(self):
        return self.filter(left_at__isnull=False)

    def enrolled_on(self, day):
        return self.filter(
            Q(joined_at__isnull=True) | Q(joined_at__lte=day),
            Q(left_at__isnull=True) | Q(left_at__gt=day),
        )

    def active_accounts(self):
        """Leaves out soft-deleted student accounts."""
        return self.filter(student__user__deleted=False)

    def class_list(self, semester):
        """
        Whom a semester's (or its courses') lists and numbers count: its current members while
        it is active; once finished, everyone who was in it (promotion marks them as left).
        """
        members = self.active_accounts()
        return members.current() if semester.is_active else members


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
    joined_at = models.DateField(null=True, blank=True)  # null = from the start
    left_at = models.DateField(null=True, blank=True)    # null = still a member

    objects = MembershipQuerySet.as_manager()

    class Meta:
        unique_together = ('student', 'classroom')

    @property
    def is_current(self):
        return self.left_at is None

    def covers(self, day):
        """Was the student a member on this date?"""
        return (self.joined_at is None or self.joined_at <= day) and (self.left_at is None or day < self.left_at)

    def counts_on(self, day, has_log) -> bool:
        """
        Does a course's class on this date count for the student (held for them)? Inside the
        membership; on the join day only if they have a log for it that day: a class held
        before they were added that day has none (everything run after they joined logs them).
        """
        return self.covers(day) and (day != self.joined_at or has_log)

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
