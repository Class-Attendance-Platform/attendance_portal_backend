import uuid
from django.db import models


class HardwareDevice(models.Model):
    """Registered ESP32 attendance devices."""
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_name = models.CharField(max_length=100)
    api_key     = models.CharField(max_length=64, unique=True)   # ESP32 authenticates with this
    last_ping   = models.DateTimeField(null=True, blank=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.device_name} ({"active" if self.is_active else "inactive"})'


class FingerprintEnrollmentRequest(models.Model):
    class Status(models.TextChoices):
        PENDING   = 'PENDING',   'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED    = 'FAILED',    'Failed'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student         = models.ForeignKey(
        'users.StudentProfile',
        on_delete=models.CASCADE,
        related_name='enrollment_requests'
    )
    hardware_device = models.ForeignKey(
        HardwareDevice,
        on_delete=models.CASCADE,
        related_name='enrollment_requests'
    )
    status       = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.student} → {self.hardware_device} [{self.status}]'
