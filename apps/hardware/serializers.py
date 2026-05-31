from rest_framework import serializers
from .models import HardwareDevice, FingerprintEnrollmentRequest


class HardwareDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model  = HardwareDevice
        fields = ['id', 'device_name', 'api_key', 'last_ping', 'is_active', 'created_at']
        read_only_fields = ['id', 'api_key', 'last_ping', 'created_at']


class EnrollmentRequestSerializer(serializers.ModelSerializer):
    student_name   = serializers.CharField(source='student.user.get_full_name', read_only=True)
    student_number = serializers.IntegerField(source='student.student_id', read_only=True)
    device_name    = serializers.CharField(source='hardware_device.device_name', read_only=True)

    class Meta:
        model  = FingerprintEnrollmentRequest
        fields = [
            'id', 'status', 'created_at', 'completed_at',
            'student_name', 'student_number', 'device_name',
            'hardware_device',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'completed_at']


# ── Payloads from ESP32 ───────────────────────────────────────────────────────

class HardwareSyncSerializer(serializers.Serializer):
    """Fingerprint scan → attendance log."""
    student_id     = serializers.IntegerField()   # numeric student_id e.g. 2302001
    course_info_id = serializers.UUIDField()
    timestamp      = serializers.DateTimeField(required=False)


class EnrollCompleteSerializer(serializers.Serializer):
    """Hardware confirms fingerprint enrollment is done."""
    enrollment_request_id = serializers.UUIDField()
    finger_id             = serializers.IntegerField()  # template slot on the hardware


# ── Student initiates enrollment ──────────────────────────────────────────────

class EnrollStartSerializer(serializers.Serializer):
    hardware_device_id = serializers.UUIDField()
