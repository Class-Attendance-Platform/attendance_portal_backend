import secrets
import uuid as uuid_lib
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.users.permissions import (
    IsAdminOrTeacher, IsStudent, can_manage_course, not_your_course_response,
)
from apps.users.models import StudentProfile, DeviceBinding
from apps.academic.models import CourseInfo, StudentClassroom
from apps.attendance.models import AttendanceSession, AttendanceLog
from apps.attendance.serializers import (
    StartSessionSerializer, ManualMarkSerializer,
    QRCheckinSerializer, AttendanceLogSerializer,
)
from apps.attendance import redis_service
from apps.attendance.services import finalize_session, finalize_expired_sessions, session_has_ended


def busy_response():
    return Response(
        {'success': False, 'message': 'Too many requests at once. Please try again.'}, status=503
    )


class StartSessionView(APIView):
    """Teacher starts an attendance session."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request):
        serializer = StartSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data
        ci = get_object_or_404(CourseInfo, id=data['course_info_id'], deleted=False)
        if not can_manage_course(request.user, ci):
            return not_your_course_response()

        # Save and close any session still running for this course
        try:
            for act_sess in AttendanceSession.objects.filter(course_info=ci, is_active=True):
                finalize_session(act_sess)
        except redis_service.SessionBusy:
            return busy_response()

        qr_token = None
        if data['mode'] in (AttendanceSession.Mode.QR_ONLINE, AttendanceSession.Mode.QR_OFFLINE):
            qr_token = secrets.token_urlsafe(32)

        # Live data first, so an active session row never exists without it
        # (otherwise a concurrent lookup could treat the new session as ended).
        session_id = uuid_lib.uuid4()
        redis_service.create_session_cache(
            session_id=str(session_id),
            course_info_id=str(ci.id),
            mode=data['mode'],
            duration_seconds=data['duration_seconds'],
        )
        try:
            session = AttendanceSession.objects.create(
                id=session_id,
                course_info=ci,
                date=timezone.localdate(),
                mode=data['mode'],
                duration_seconds=data['duration_seconds'],
                qr_token=qr_token,
            )
        except Exception:
            redis_service.delete_session_cache(str(session_id))
            raise

        return Response({
            'success': True,
            'session': {
                'id': str(session.id),
                'course_info_id': str(ci.id),
                'mode': session.mode,
                'date': str(session.date),
                'duration_seconds': session.duration_seconds,
                'qr_token': qr_token,
                'time_left': data['duration_seconds'],
            }
        }, status=201)


class StopSessionView(APIView):
    """Teacher stops session — commits all Redis submissions to DB."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session = get_object_or_404(AttendanceSession.objects.select_related('course_info'), id=uuid)
        if not can_manage_course(request.user, session.course_info):
            return not_your_course_response()

        # Also fine when the timer or a status poll already saved it.
        try:
            finalize_session(session)
        except redis_service.SessionBusy:
            return busy_response()

        present_count = AttendanceLog.objects.filter(
            session=session, status=AttendanceLog.Status.PRESENT
        ).count()

        return Response({
            'success': True,
            'message': 'Session stopped and attendance committed.',
            'total_present': present_count,
        })


class SessionStatusView(APIView):
    """Get live session status and current submissions."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        session = get_object_or_404(AttendanceSession.objects.select_related('course_info'), id=uuid)
        if not can_manage_course(request.user, session.course_info):
            return not_your_course_response()

        cache_data = redis_service.get_session_cache(str(session.id))

        if not session.is_active or session_has_ended(session, cache_data):
            # Timer ran out or session was stopped: make sure it is saved
            try:
                finalize_session(session)
            except redis_service.SessionBusy:
                return busy_response()
            return Response({
                'success': True,
                'active': False,
                'session_id': str(session.id),
            })

        if cache_data is None:
            # Live data missing although the timer still runs: report from the database
            ends_at = session.started_at + timedelta(seconds=session.duration_seconds)
            time_left = max(0, int((ends_at - timezone.now()).total_seconds()))
            submissions = {}
        else:
            time_left = redis_service.time_left(str(session.id))
            submissions = cache_data.get('submissions', {})
        return Response({
            'success': True,
            'active': True,
            'session': {
                'id': str(session.id),
                'mode': session.mode,
                'time_left': time_left,
                'submissions': [
                    {'student_id': int(sid), 'name': info['name']}
                    for sid, info in submissions.items()
                ],
            }
        })


class QROnlineCheckinView(APIView):
    """Student submits attendance via QR online."""
    permission_classes = [IsStudent]

    def post(self, request, uuid):
        session = get_object_or_404(AttendanceSession, id=uuid, mode=AttendanceSession.Mode.QR_ONLINE)
        serializer = QRCheckinSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data

        # Verify QR token
        if session.qr_token != data['qr_token']:
            return Response({'success': False, 'message': 'Invalid QR token.'}, status=403)

        # Verify session is still accepting check-ins
        cache_data = redis_service.get_session_cache(str(session.id))
        if not session.is_active or not redis_service.is_open(cache_data):
            return Response({'success': False, 'message': 'Session has expired.'}, status=410)

        # A student can only check in for themselves
        student = StudentProfile.objects.filter(user=request.user, user__deleted=False).first()
        if not student:
            return Response({'success': False, 'message': 'Student not found.'}, status=404)
        if data.get('student_id') not in (None, student.student_id):
            return Response(
                {'success': False, 'message': 'You can only submit your own attendance.'}, status=403
            )
        if not StudentClassroom.objects.filter(
            student=student, classroom_id=session.course_info.classroom_id
        ).exists():
            return Response({'success': False, 'message': 'You are not enrolled in this course.'}, status=403)

        # Verify device binding
        mac = data['mac_address']
        binding = DeviceBinding.objects.filter(student=student, is_active=True).first()

        if not binding:
            # First time — create binding
            # Reject if this MAC is already bound to another student
            if DeviceBinding.objects.filter(mac_address=mac, is_active=True).exists():
                return Response({'success': False, 'message': 'This device is already bound to another student.'}, status=403)
            DeviceBinding.objects.create(student=student, mac_address=mac)
        elif binding.mac_address != mac:
            # For web compatibility, just update the mac address instead of blocking
            binding.mac_address = mac
            binding.save()

        # Add to Redis session
        try:
            added = redis_service.add_submission(
                session_id=str(session.id),
                student_int_id=student.student_id,
                student_name=student.user.get_full_name(),
                mac=mac,
            )
        except redis_service.SessionBusy:
            return busy_response()

        if not added:
            return Response({'success': False, 'message': 'Already submitted or session expired.'}, status=409)

        return Response({'success': True, 'message': 'Attendance recorded.'})


class ManualMarkView(APIView):
    """Teacher manually marks or modifies a student's attendance for an active session."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session = get_object_or_404(AttendanceSession.objects.select_related('course_info'), id=uuid)
        if not can_manage_course(request.user, session.course_info):
            return not_your_course_response()

        serializer = ManualMarkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data
        student = get_object_or_404(StudentProfile, id=data['student_id'], user__deleted=False)
        if not StudentClassroom.objects.filter(
            student=student, classroom_id=session.course_info.classroom_id
        ).exists():
            return Response({'success': False, 'message': 'This student is not in this course.'}, status=400)

        log, created = AttendanceLog.objects.update_or_create(
            session=session,
            student=student,
            defaults={
                'course_info': session.course_info,
                'date': session.date,
                'time': timezone.localtime().time(),
                'status': data['status'],
                'source': AttendanceLog.Source.MANUAL,
                'is_modified_by_teacher': True,
                'notes': data.get('notes', ''),
            }
        )

        return Response({
            'success': True,
            'created': created,
            'log': AttendanceLogSerializer(log).data,
        })


class CourseAttendanceHistoryView(APIView):
    """Returns all attendance logs for a course_info, optionally filtered by date."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        if not can_manage_course(request.user, ci):
            return not_your_course_response()

        finalize_expired_sessions(ci)
        logs = AttendanceLog.objects.filter(course_info=ci).select_related('student__user')

        date_filter = request.query_params.get('date')
        if date_filter:
            logs = logs.filter(date=date_filter)

        student_filter = request.query_params.get('student_id')
        if student_filter:
            logs = logs.filter(student__id=student_filter)

        # Group by date
        grouped = defaultdict(list)
        for log in logs:
            grouped[str(log.date)].append(AttendanceLogSerializer(log).data)

        return Response({
            'success': True,
            'course_info_id': str(uuid),
            'history': [
                {'date': date, 'logs': entries}
                for date, entries in sorted(grouped.items(), reverse=True)
            ],
        })


class ActiveSessionView(APIView):
    """Get active session details for a course to allow direct give attendance."""
    permission_classes = [IsStudent]

    def get(self, request, uuid):
        # Only students enrolled in this course's classroom see its session
        enrolled = StudentClassroom.objects.filter(
            student__user=request.user,
            classroom__course_infos__id=uuid,
        ).exists()
        if not enrolled:
            return Response({'success': False, 'message': 'No active session.'})

        finalize_expired_sessions(uuid)
        session = AttendanceSession.objects.filter(course_info_id=uuid, is_active=True).first()
        if session:
            return Response({
                'success': True,
                'session_id': str(session.id),
                'qr_token': session.qr_token
            })
        return Response({'success': False, 'message': 'No active session.'})
