import secrets
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.users.permissions import IsTeacher, IsAdminOrTeacher, IsStudent
from apps.users.models import StudentProfile, DeviceBinding
from apps.academic.models import CourseInfo
from apps.attendance.models import AttendanceSession, AttendanceLog
from apps.attendance.serializers import (
    StartSessionSerializer, ManualMarkSerializer,
    QRCheckinSerializer, AttendanceLogSerializer,
)
from apps.attendance import redis_service
from apps.attendance.services import commit_session_to_db


class StartSessionView(APIView):
    """Teacher starts an attendance session."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request):
        serializer = StartSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data
        ci = get_object_or_404(CourseInfo, id=data['course_info_id'], deleted=False)

        # Stop and finalize any existing active sessions for this course to prevent duplicates/errors
        active_sessions = AttendanceSession.objects.filter(course_info=ci, is_active=True)
        for act_sess in active_sessions:
            submissions = redis_service.get_submissions(str(act_sess.id))
            redis_service.delete_session_cache(str(act_sess.id))

            act_sess.is_active = False
            act_sess.ended_at = timezone.now()
            act_sess.save()

            if submissions:
                commit_session_to_db(act_sess, submissions)
            else:
                # Redis expired — fill ABSENT for anyone not already logged
                from apps.attendance.models import AttendanceLog as AL
                from apps.academic.models import StudentClassroom
                enrolled = StudentClassroom.objects.filter(
                    classroom=act_sess.course_info.classroom
                ).select_related('student')
                already_logged = set(
                    AL.objects.filter(session=act_sess).values_list('student_id', flat=True)
                )
                logs_to_create = []
                for membership in enrolled:
                    if membership.student.id not in already_logged:
                        logs_to_create.append(AL(
                            session=act_sess,
                            course_info=act_sess.course_info,
                            student=membership.student,
                            date=act_sess.date,
                            status=AL.Status.ABSENT,
                            source=act_sess.mode,
                        ))
                AL.objects.bulk_create(logs_to_create, ignore_conflicts=True)

        qr_token = None
        if data['mode'] in (AttendanceSession.Mode.QR_ONLINE, AttendanceSession.Mode.QR_OFFLINE):
            qr_token = secrets.token_urlsafe(32)

        session = AttendanceSession.objects.create(
            course_info=ci,
            date=timezone.now().date(),
            mode=data['mode'],
            duration_seconds=data['duration_seconds'],
            qr_token=qr_token,
        )

        redis_service.create_session_cache(
            session_id=str(session.id),
            course_info_id=str(ci.id),
            mode=data['mode'],
            duration_seconds=data['duration_seconds'],
        )

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
        session = get_object_or_404(AttendanceSession, id=uuid, is_active=True)

        submissions = redis_service.get_submissions(str(session.id))
        redis_service.delete_session_cache(str(session.id))

        session.is_active = False
        session.ended_at = timezone.now()
        session.save()

        if submissions:
            commit_session_to_db(session, submissions)
        else:
            # Redis expired — fill ABSENT for anyone not already logged
            from apps.attendance.models import AttendanceLog as AL
            from apps.academic.models import StudentClassroom
            enrolled = StudentClassroom.objects.filter(
                classroom=session.course_info.classroom
            ).select_related('student')
            already_logged = set(
                AL.objects.filter(session=session).values_list('student_id', flat=True)
            )
            logs_to_create = []
            for membership in enrolled:
                if membership.student.id not in already_logged:
                    logs_to_create.append(AL(
                        session=session,
                        course_info=session.course_info,
                        student=membership.student,
                        date=session.date,
                        status=AL.Status.ABSENT,
                        source=session.mode,
                    ))
            AL.objects.bulk_create(logs_to_create, ignore_conflicts=True)

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
        session = get_object_or_404(AttendanceSession, id=uuid)
        cache_data = redis_service.get_session_cache(str(session.id))

        if not cache_data:
            # Session expired or was stopped
            if session.is_active:
                session.is_active = False
                session.save()
            return Response({
                'success': True,
                'active': False,
                'session_id': str(session.id),
            })

        submissions = cache_data.get('submissions', {})
        return Response({
            'success': True,
            'active': True,
            'session': {
                'id': str(session.id),
                'mode': session.mode,
                'time_left': redis_service.time_left(str(session.id)),
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

        # Verify session is active in Redis
        cache_data = redis_service.get_session_cache(str(session.id))
        if not cache_data:
            return Response({'success': False, 'message': 'Session has expired.'}, status=410)

        # Get student profile
        try:
            student = StudentProfile.objects.get(student_id=data['student_id'], user__deleted=False)
        except StudentProfile.DoesNotExist:
            return Response({'success': False, 'message': 'Student not found.'}, status=404)

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
        added = redis_service.add_submission(
            session_id=str(session.id),
            student_int_id=student.student_id,
            student_name=student.user.get_full_name(),
            mac=mac,
        )

        if not added:
            return Response({'success': False, 'message': 'Already submitted or session expired.'}, status=409)

        return Response({'success': True, 'message': 'Attendance recorded.'})


class ManualMarkView(APIView):
    """Teacher manually marks or modifies a student's attendance for an active session."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session = get_object_or_404(AttendanceSession, id=uuid)
        serializer = ManualMarkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'success': False, 'errors': serializer.errors}, status=400)

        data = serializer.validated_data
        student = get_object_or_404(StudentProfile, id=data['student_id'], user__deleted=False)

        log, created = AttendanceLog.objects.update_or_create(
            session=session,
            student=student,
            defaults={
                'course_info': session.course_info,
                'date': session.date,
                'time': timezone.now().time(),
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
        logs = AttendanceLog.objects.filter(course_info=ci).select_related('student__user')

        date_filter = request.query_params.get('date')
        if date_filter:
            logs = logs.filter(date=date_filter)

        student_filter = request.query_params.get('student_id')
        if student_filter:
            logs = logs.filter(student__id=student_filter)

        # Group by date
        from collections import defaultdict
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
        session = AttendanceSession.objects.filter(course_info_id=uuid, is_active=True).first()
        if session:
            return Response({
                'success': True, 
                'session_id': str(session.id), 
                'qr_token': session.qr_token
            })
        return Response({'success': False, 'message': 'No active session.'})

