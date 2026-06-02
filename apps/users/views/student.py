from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.users.models import StudentProfile, DeviceBinding
from apps.users.permissions import IsStudent


class StudentAttendanceView(APIView):
    """Returns all semesters + attendance for a student. Delegated to attendance app."""
    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        profile = get_object_or_404(StudentProfile, id=uuid)
        # Only the student themselves or admin can view
        if request.user.role == 'STUDENT' and request.user.student_profile.id != profile.id:
            return Response({'success': False, 'message': 'Forbidden.'}, status=403)

        # Import here to avoid circular dependency
        from apps.attendance.services import get_student_attendance_summary
        data = get_student_attendance_summary(profile)
        return Response({'success': True, 'semesters': data})


class StudentDeviceBindingView(APIView):
    """Used by the offline server to verify a device binding."""
    permission_classes = [IsAuthenticated]

    def get(self, request, student_id):
        """Verify whether a mac_address is bound to this student."""
        mac = request.query_params.get('mac_address')
        if not mac:
            return Response({'success': False, 'message': 'mac_address required.'}, status=400)

        profile = get_object_or_404(StudentProfile, student_id=student_id)
        binding = DeviceBinding.objects.filter(student=profile, is_active=True).first()

        base = {
            'student_id':   student_id,
            'profile_uuid': str(profile.id),
            'name':         profile.user.get_full_name(),
        }

        if not binding:
            return Response({'success': True, 'status': 'unbound', **base})

        if binding.mac_address == mac:
            return Response({'success': True, 'status': 'verified', **base})

        return Response({'success': False, 'status': 'mismatch', 'message': 'Device not bound to this student.'}, status=403)
