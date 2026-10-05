from rest_framework import serializers

from .models import AttendanceSession, AttendanceLog

DURATION_CHOICES = (2, 5, 10, 15)


class StartSessionSerializer(serializers.Serializer):
    course_info_id   = serializers.UUIDField()
    delivery         = serializers.ChoiceField(
        choices=AttendanceSession.Delivery.choices, default=AttendanceSession.Delivery.IN_CLASS,
    )
    duration_minutes = serializers.ChoiceField(
        choices=DURATION_CHOICES, default=5,
        error_messages={'invalid_choice': 'Choose 2, 5, 10 or 15 minutes.'},
    )
    # Live sessions are QR_ONLINE; FINGERPRINT and QR_OFFLINE stay for the hidden fingerprint
    # devices and the offline laptop server. Face attendance is saved through /api/faces/confirm/.
    mode             = serializers.ChoiceField(
        choices=[c for c in AttendanceSession.Mode.choices if c[0] != AttendanceSession.Mode.FACE],
        default=AttendanceSession.Mode.QR_ONLINE,
    )


class ExtendSessionSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=1, max_value=28, default=2)


class LiveMarkSerializer(serializers.Serializer):
    profile_id = serializers.UUIDField()


class CheckInSerializer(serializers.Serializer):
    code       = serializers.CharField(max_length=20, trim_whitespace=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)
    device_id  = serializers.CharField(max_length=200, trim_whitespace=True)


class CorrectionSerializer(serializers.Serializer):
    date       = serializers.DateField()
    profile_id = serializers.UUIDField()
    status     = serializers.ChoiceField(choices=[AttendanceLog.Status.PRESENT, AttendanceLog.Status.ABSENT])


class RollCallSerializer(serializers.Serializer):
    date               = serializers.DateField()
    present_profile_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)
    # The `version` of the roll call list the page showed (GET .../roll-call/?date=); optional
    version            = serializers.CharField(required=False, allow_blank=False, max_length=64)
