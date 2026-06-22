from rest_framework import serializers
from .models import AttendanceSession, AttendanceLog


class StartSessionSerializer(serializers.Serializer):
    course_info_id   = serializers.UUIDField()
    mode             = serializers.ChoiceField(choices=AttendanceSession.Mode.choices)
    duration_seconds = serializers.IntegerField(min_value=30, max_value=7200, default=300)


class ManualMarkSerializer(serializers.Serializer):
    student_id = serializers.UUIDField()          # StudentProfile UUID
    status     = serializers.ChoiceField(choices=AttendanceLog.Status.choices)
    notes      = serializers.CharField(required=False, allow_blank=True, default='')


class QRCheckinSerializer(serializers.Serializer):
    student_id  = serializers.IntegerField()      # numeric student_id e.g. 2302001
    mac_address = serializers.CharField(max_length=17)
    qr_token    = serializers.CharField()


class AttendanceLogSerializer(serializers.ModelSerializer):
    student_name       = serializers.CharField(source='student.user.get_full_name', read_only=True)
    student_number     = serializers.IntegerField(source='student.student_id', read_only=True)
    student_id         = serializers.UUIDField(source='student.id', read_only=True)

    class Meta:
        model  = AttendanceLog
        fields = [
            'id', 'date', 'time', 'status', 'source',
            'is_modified_by_teacher', 'notes',
            'student_name', 'student_number', 'student_id',
        ]


class AttendanceSessionSerializer(serializers.ModelSerializer):
    logs = AttendanceLogSerializer(many=True, read_only=True)

    class Meta:
        model  = AttendanceSession
        fields = ['id', 'date', 'mode', 'started_at', 'ended_at', 'is_active', 'duration_seconds', 'logs']
