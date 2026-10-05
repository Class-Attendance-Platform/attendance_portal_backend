"""
Fills AttendanceLog.method for logs saved before it existed (API v2 section 9):
- teacher-made logs (source MANUAL: roll calls, manual marks) -> TEACHER
- present logs of QR sessions -> QR, of face sessions -> FACE, of fingerprint devices -> FINGERPRINT
- absent logs of QR, face and fingerprint sessions stay '' (not checked in by any method)
"""
from django.db import migrations

PRESENT_METHOD_FOR_SOURCE = {
    'QR_ONLINE': 'QR',
    'QR_OFFLINE': 'QR',
    'FACE': 'FACE',
    'HARDWARE': 'FINGERPRINT',
}


def fill_methods(apps, schema_editor):
    AttendanceLog = apps.get_model('attendance', 'AttendanceLog')
    blank = AttendanceLog.objects.filter(method='')
    blank.filter(source='MANUAL').update(method='TEACHER')
    for source, method in PRESENT_METHOD_FOR_SOURCE.items():
        blank.filter(source=source, status='PRESENT').update(method=method)


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0003_live_code_corrections'),
    ]

    operations = [
        migrations.RunPython(fill_methods, migrations.RunPython.noop),
    ]
