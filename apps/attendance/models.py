import uuid
from django.db import models


class AttendanceSession(models.Model):
    class Mode(models.TextChoices):
        FINGERPRINT  = 'FINGERPRINT',  'Fingerprint'
        QR_ONLINE    = 'QR_ONLINE',    'QR Online'
        QR_OFFLINE   = 'QR_OFFLINE',   'QR Offline'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_info    = models.ForeignKey(
        'academic.CourseInfo', on_delete=models.CASCADE, related_name='sessions'
    )
    date           = models.DateField()
    mode           = models.CharField(max_length=15, choices=Mode.choices)
    started_at     = models.DateTimeField(auto_now_add=True)
    ended_at       = models.DateTimeField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    duration_seconds = models.PositiveIntegerField(default=300)
    # Signed token embedded in the QR code (null for FINGERPRINT mode)
    qr_token       = models.CharField(max_length=255, null=True, blank=True, unique=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.course_info} | {self.date} | {self.mode}'


class AttendanceLog(models.Model):
    class Status(models.TextChoices):
        PRESENT = 'PRESENT', 'Present'
        ABSENT  = 'ABSENT',  'Absent'
        LATE    = 'LATE',    'Late'

    class Source(models.TextChoices):
        HARDWARE    = 'HARDWARE',    'Hardware'
        QR_ONLINE   = 'QR_ONLINE',   'QR Online'
        QR_OFFLINE  = 'QR_OFFLINE',  'QR Offline'
        MANUAL      = 'MANUAL',      'Manual'

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
    is_modified_by_teacher = models.BooleanField(default=False)
    notes       = models.TextField(blank=True, default='')

    class Meta:
        ordering  = ['-date', '-time']
        unique_together = ('session', 'student')   # one log per student per session

    def __str__(self):
        return f'{self.student} | {self.course_info} | {self.date} | {self.status}'
