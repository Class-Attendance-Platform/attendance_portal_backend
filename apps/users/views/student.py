from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.users.models import StudentProfile, DeviceBinding
from apps.users.permissions import IsAdminOrTeacher
from config.errors import error_response


class StudentAttendanceView(APIView):
    """Returns all semesters + attendance for a student. Delegated to attendance app."""
    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        from django.db.models import Q
        profile = get_object_or_404(StudentProfile, Q(id=uuid) | Q(user_id=uuid))
        # A student sees only their own attendance; admins may look at anyone's. Teachers see
        # students only through their own courses (section 6), never all of a student's courses.
        if request.user.role == 'TEACHER':
            return error_response(
                "Teachers see a student's attendance in their own courses.", status=403, code='permission_denied',
            )
        if request.user.role == 'STUDENT' and profile.user_id != request.user.id:
            return error_response('You can only view your own attendance.', status=403, code='permission_denied')

        # Import here to avoid circular dependency
        from apps.attendance.records import get_student_attendance_summary
        data = get_student_attendance_summary(profile)
        return Response({'success': True, 'semesters': data})


class StudentDeviceBindingView(APIView):
    """
    Used by the offline server (signed in with the teacher's token) to verify a device
    binding. Teachers and admins only: it returns any student's name and profile id.
    """
    permission_classes = [IsAdminOrTeacher]

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
