import secrets
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.users.permissions import IsAdmin, IsStudent
from apps.users.models import StudentProfile
from apps.academic.models import CourseInfo
from apps.attendance.models import AttendanceSession, AttendanceLog
from apps.attendance import redis_service

from apps.hardware.models import HardwareDevice, FingerprintEnrollmentRequest
from apps.hardware.authentication import HardwareAPIKeyAuthentication
from apps.hardware.permissions import IsHardwareDevice
from apps.hardware.serializers import (
    HardwareDeviceSerializer,
    HardwareSyncSerializer,
    EnrollStartSerializer,
    EnrollCompleteSerializer,
    EnrollmentRequestSerializer,
)


# ── Admin — Device Management ─────────────────────────────────────────────────

class HardwareDeviceListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        devices = HardwareDevice.objects.all()
        return Response({'success': True, 'devices': HardwareDeviceSerializer(devices, many=True).data})

    def post(self, request):
        name = request.data.get('device_name', '').strip()
        if not name:
            return Response({'success': False, 'message': 'device_name is required.'}, status=400)

        api_key = secrets.token_hex(32)   # 64-char hex key
        device  = HardwareDevice.objects.create(device_name=name, api_key=api_key)
        return Response({
            'success': True,
            'device': HardwareDeviceSerializer(device).data,
            # Only time the full api_key is returned — admin must flash this to ESP32
            'api_key': api_key,
        }, status=201)


class HardwareDeviceDetailView(APIView):
    permission_classes = [IsAdmin]

    def patch(self, request, uuid):
        device = get_object_or_404(HardwareDevice, id=uuid)
        name      = request.data.get('device_name')
        is_active = request.data.get('is_active')
        if name:
            device.device_name = name
        if is_active is not None:
            device.is_active = bool(is_active)
        device.save()
        return Response({'success': True, 'device': HardwareDeviceSerializer(device).data})

    def delete(self, request, uuid):
        device = get_object_or_404(HardwareDevice, id=uuid)
        device.is_active = False
        device.save()
        return Response({'success': True, 'message': 'Device deactivated.'})


# ── ESP32 Webhook — Fingerprint Attendance ────────────────────────────────────

class HardwareSyncView(APIView):
    """
    Called by ESP32 when a fingerprint is scanned during an active session.
    Auth: X-Hardware-Key header.
    """
    authentication_classes = [HardwareAPIKeyAuthentication]
    permission_classes     = [IsHardwareDevice]

    def post(self, request):
        serializer = HardwareSyncSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data       = serializer.validated_data
        student_id = data['student_id']
        ci_id      = data['course_info_id']

        # Look up student by hardware numeric id
        try:
            student = StudentProfile.objects.get(student_id=student_id, user__deleted=False)
        except StudentProfile.DoesNotExist:
            return Response({'success': False, 'message': f'No student with id {student_id}.'}, status=404)

        # Find active fingerprint session for this course
        try:
            session = AttendanceSession.objects.get(
                course_info__id=ci_id,
                mode=AttendanceSession.Mode.FINGERPRINT,
                is_active=True,
            )
        except AttendanceSession.DoesNotExist:
            return Response({'success': False, 'message': 'No active fingerprint session for this course.'}, status=404)

        # Add to Redis
        added = redis_service.add_submission(
            session_id=str(session.id),
            student_int_id=student.student_id,
            student_name=student.user.get_full_name(),
            mac='hardware',
        )

        if not added:
            return Response({
                'success': False,
                'message': f'{student.user.get_full_name()} already recorded or session expired.'
            }, status=409)

        return Response({
            'success': True,
            'message': f'{student.user.get_full_name()} attendance recorded.',
            'student_id': student_id,
        })


# ── Fingerprint Enrollment ────────────────────────────────────────────────────

class EnrollStartView(APIView):
    """
    Student initiates fingerprint enrollment.
    Picks a hardware device → creates a PENDING request →
    hardware device polls for pending requests and shows student ID on screen.
    """
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = EnrollStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        student = request.user.student_profile

        # Cancel any existing pending requests for this student
        FingerprintEnrollmentRequest.objects.filter(
            student=student, status=FingerprintEnrollmentRequest.Status.PENDING
        ).update(status=FingerprintEnrollmentRequest.Status.FAILED)

        device = get_object_or_404(
            HardwareDevice,
            id=serializer.validated_data['hardware_device_id'],
            is_active=True
        )

        enrollment = FingerprintEnrollmentRequest.objects.create(
            student=student,
            hardware_device=device,
        )

        return Response({
            'success': True,
            'enrollment': EnrollmentRequestSerializer(enrollment).data,
            'message': f'Walk to device "{device.device_name}" and place your finger.',
        }, status=201)


class EnrollCompleteView(APIView):
    """
    Called by ESP32 after successfully capturing the fingerprint template.
    Auth: X-Hardware-Key header.
    """
    authentication_classes = [HardwareAPIKeyAuthentication]
    permission_classes     = [IsHardwareDevice]

    def post(self, request):
        serializer = EnrollCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data

        enrollment = get_object_or_404(
            FingerprintEnrollmentRequest,
            id=data['enrollment_request_id'],
            status=FingerprintEnrollmentRequest.Status.PENDING,
            hardware_device=request.auth,   # must match the device that's completing it
        )

        # Save finger_id to student profile
        student = enrollment.student
        student.hardware_finger_id = data['finger_id']
        student.save()

        # Mark enrollment complete
        enrollment.status       = FingerprintEnrollmentRequest.Status.COMPLETED
        enrollment.completed_at = timezone.now()
        enrollment.save()

        return Response({
            'success': True,
            'message': f'Fingerprint enrolled for {student.user.get_full_name()}.',
            'student_id': student.student_id,
            'finger_id': data['finger_id'],
        })


class EnrollStatusView(APIView):
    """Student polls this to check if enrollment is complete."""
    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        enrollment = get_object_or_404(FingerprintEnrollmentRequest, id=uuid)

        # Only the student themselves or admin can check
        if (request.user.role == 'STUDENT' and
                enrollment.student.user != request.user):
            return Response({'success': False, 'message': 'Forbidden.'}, status=403)

        return Response({
            'success': True,
            'enrollment': EnrollmentRequestSerializer(enrollment).data,
        })


class PendingEnrollmentsView(APIView):
    """
    ESP32 polls this to see if any student is waiting for enrollment on this device.
    Auth: X-Hardware-Key header.
    """
    authentication_classes = [HardwareAPIKeyAuthentication]
    permission_classes     = [IsHardwareDevice]

    def get(self, request):
        device = request.auth
        pending = FingerprintEnrollmentRequest.objects.filter(
            hardware_device=device,
            status=FingerprintEnrollmentRequest.Status.PENDING,
        ).select_related('student__user').first()

        if not pending:
            return Response({'success': True, 'pending': None})

        return Response({
            'success': True,
            'pending': {
                'enrollment_id': str(pending.id),
                'student_id': pending.student.student_id,
                'student_name': pending.student.user.get_full_name(),
            }
        })
