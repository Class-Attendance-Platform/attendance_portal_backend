from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from apps.users.models import StudentProfile, DeviceBinding
from apps.users.permissions import IsAdminOrTeacher
from config.errors import error_response

class StudentAttendanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        from django.db.models import Q
        profile = get_object_or_404(StudentProfile, Q(id=uuid) | Q(user_id=uuid))
        if request.user.role == 'TEACHER':
            return error_response(
                "Teachers see a student's attendance in their own courses.", status=403, code='permission_denied',
            )
        if request.user.role == 'STUDENT' and profile.user_id != request.user.id:
            return error_response('You can only view your own attendance.', status=403, code='permission_denied')

        from apps.academic.models import StudentClassroom
        from apps.attendance.models import AttendanceSession
        from apps.attendance.records import get_student_attendance_summary
        from apps.attendance.services import finalize_ended_sessions

        finalize_ended_sessions(AttendanceSession.objects.filter(
            course_info__classroom_id__in=StudentClassroom.objects.filter(student=profile).values('classroom_id'),
        ))
        data = get_student_attendance_summary(profile)
        return Response({'success': True, 'semesters': data})

class StudentDeviceBindingView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, student_id):
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
