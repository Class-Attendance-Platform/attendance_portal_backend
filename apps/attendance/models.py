import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AttendanceSession(models.Model):
    class Mode(models.TextChoices):
        FINGERPRINT  = 'FINGERPRINT',  'Fingerprint'
        QR_ONLINE    = 'QR_ONLINE',    'QR Online'
        QR_OFFLINE   = 'QR_OFFLINE',   'QR Offline'
        FACE         = 'FACE',         'Face'

    class Delivery(models.TextChoices):
        IN_CLASS = 'IN_CLASS', 'In class'
        ONLINE   = 'ONLINE',   'Online'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_info    = models.ForeignKey(
        'academic.CourseInfo', on_delete=models.CASCADE, related_name='sessions'
    )
    date           = models.DateField()
    mode           = models.CharField(max_length=15, choices=Mode.choices)
    # Where the class is (live sessions); the logs keep their source (QR_ONLINE) either way
    delivery       = models.CharField(max_length=10, choices=Delivery.choices, default=Delivery.IN_CLASS)
    started_at     = models.DateTimeField(auto_now_add=True)
    ended_at       = models.DateTimeField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    # Total length, extensions included (the live timer is in Redis; this is the fallback)
    duration_seconds = models.PositiveIntegerField(default=300)
    # Secret behind the rotating 6-digit check-in code (null for FINGERPRINT mode); never sent to students
    qr_token       = models.CharField(max_length=255, null=True, blank=True, unique=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.course_info} | {self.date} | {self.mode}'


class AttendanceLog(models.Model):
    class Status(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT  = 'ABSENT',  'Absent'
        LATE    = 'LATE',    'Late'   # old data only; counts as not present

    class Source(models.TextChoices):
        HARDWARE    = 'HARDWARE',    'Hardware'
        QR_ONLINE   = 'QR_ONLINE',   'QR Online'
        QR_OFFLINE  = 'QR_OFFLINE',  'QR Offline'
        MANUAL      = 'MANUAL',      'Manual'
        FACE        = 'FACE',        'Face'

    class Method(models.TextChoices):
        """How the student was marked present ('' = not checked in by any method)."""
        QR          = 'QR',          'QR code'
        CODE        = 'CODE',        'Typed code'
        FACE        = 'FACE',        'Face'
        TEACHER     = 'TEACHER',     'Teacher'
        FINGERPRINT = 'FINGERPRINT', 'Fingerprint'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session     = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name='logs', null=True, blank=True
    )
    course_info = models.ForeignKey(
        'academic.CourseInfo', on_delete=models.CASCADE, related_name='attendance_logs'
    )
    student     = models.ForeignKey(
        'users.StudentProfile', on_delete=models.CASCADE, related_name='attendance_logs'
    )
    date        = models.DateField()
    time        = models.TimeField(null=True, blank=True)
    status      = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    source      = models.CharField(max_length=15, choices=Source.choices, default=Source.MANUAL)
    method      = models.CharField(max_length=12, choices=Method.choices, blank=True, default='')
    is_modified_by_teacher = models.BooleanField(default=False)
    # The last correction (the full trail is in AttendanceChange)
    changed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    changed_at  = models.DateTimeField(null=True, blank=True)
    notes       = models.TextField(blank=True, default='')

    class Meta:
        ordering  = ['-date', '-time']
        unique_together = ('session', 'student')   # one log per student per session

    def __str__(self):
        return f'{self.student} | {self.course_info} | {self.date} | {self.status}'


class AttendanceChange(models.Model):
    """One correction of a log: old -> new status, who and when (old_status '' = no log before)."""
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    log         = models.ForeignKey(AttendanceLog, on_delete=models.CASCADE, related_name='changes')
    old_status  = models.CharField(max_length=10, choices=AttendanceLog.Status.choices, blank=True, default='')
    new_status  = models.CharField(max_length=10, choices=AttendanceLog.Status.choices)
    changed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    changed_at  = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f'{self.log} | {self.old_status or "-"} -> {self.new_status}'
