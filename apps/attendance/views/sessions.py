"""
Live attendance sessions with the rotating 6-digit code (API v2 section 5) and the
course history (section 6).
"""
import re
import secrets
import unicodedata
import uuid as uuid_lib
from collections import defaultdict

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo, Semester, StudentClassroom
from apps.academic.serializers import person_name
from apps.attendance import codes, redis_service
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.records import day_status, log_details, parse_day
from apps.attendance.serializers import (
    CheckInSerializer, ExtendSessionSerializer, LiveMarkSerializer, StartSessionSerializer,
)
from apps.attendance.services import (
    MAX_SESSION_SECONDS, can_join_live, cancel_session, enrolled_memberships, finalize_expired_sessions,
    finalize_session, live_sessions, local_datetime, session_ends_at, session_is_live, session_payload,
    session_time_left,
)
from apps.users.models import StudentProfile
from apps.users.permissions import IsAdminOrTeacher, IsStudent, can_manage_course, not_your_course_response
from apps.users.throttles import CheckInThrottle
from config.errors import error_response, validation_error_response

QR_MODES = (AttendanceSession.Mode.QR_ONLINE, AttendanceSession.Mode.QR_OFFLINE)
CODE_PATTERN = re.compile(r'^[0-9]{6}$')


def normalize_code(text) -> str:
    """The typed code without spaces or dashes, other scripts' digits (e.g. Bengali ২৩৪) as 0-9."""
    text = re.sub(r'[\s-]', '', text)
    return ''.join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in text)


def busy_response():
    return error_response('Too many requests at once. Please try again.', status=503, code='busy')


def session_ended_response(message='This session has ended.'):
    return error_response(message, status=410, code='session_ended')


def semester_finished_response():
    """No new live or face sessions for a finished semester (roll call and corrections stay)."""
    return error_response(
        'This semester is finished. Use roll call to add or fix a class.', status=400, code='semester_finished',
    )


def cancelled_response():
    return error_response('This session was cancelled.', status=404, code='not_found')


def managed_session(request, uuid):
    """(session, None) for the course's teacher or an admin, else (None, error response)."""
    session = get_object_or_404(AttendanceSession.objects.select_related('course_info'), id=uuid)
    if not can_manage_course(request.user, session.course_info):
        return None, not_your_course_response()
    return session, None


def student_brief(student) -> dict:
    return {'profile_id': str(student.id), 'student_id': student.student_id, 'name': person_name(student.user)}


class StartSessionView(APIView):
    """Teacher starts a live session: {course_info_id, delivery, duration_minutes}."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request):
        serializer = StartSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        data = serializer.validated_data
        ci = get_object_or_404(
            CourseInfo.objects.select_related('semester'),
            id=data['course_info_id'], deleted=False, course__deleted=False, semester__deleted=False,
        )
        if not can_manage_course(request.user, ci):
            return not_your_course_response()
        if not ci.semester.is_active:
            return semester_finished_response()

        duration = data['duration_minutes'] * 60
        try:
            with transaction.atomic():
                # One start at a time (only this row: no Meta ordering joins under FOR UPDATE)
                CourseInfo.objects.select_for_update().filter(pk=ci.pk).order_by().first()
                # Finished meanwhile (read again under the lock)
                if not Semester.objects.filter(pk=ci.semester_id, is_active=True).exists():
                    return semester_finished_response()
                # A live session blocks a new one; an ended one is saved first.
                for running in AttendanceSession.objects.filter(course_info=ci, is_active=True):
                    if session_is_live(running, redis_service.get_session_cache(str(running.id))):
                        return error_response(
                            'A session is already running for this course.', status=409,
                            code='session_running', session_id=str(running.id),
                        )
                    finalize_session(running)

                # Live data first, so an active session row never exists without it
                # (otherwise a concurrent lookup could treat the new session as ended).
                session_id = uuid_lib.uuid4()
                redis_service.create_session_cache(
                    session_id=str(session_id), course_info_id=str(ci.id), mode=data['mode'],
                    duration_seconds=duration,
                )
                try:
                    session = AttendanceSession.objects.create(
                        id=session_id,
                        course_info=ci,
                        date=timezone.localdate(),
                        mode=data['mode'],
                        delivery=data['delivery'],
                        duration_seconds=duration,
                        qr_token=secrets.token_urlsafe(32) if data['mode'] in QR_MODES else None,
                    )
                except Exception:
                    redis_service.delete_session_cache(str(session_id))
                    raise
        except redis_service.SessionBusy:
            return busy_response()

        cache_data = redis_service.get_session_cache(str(session.id))
        return Response({'success': True, 'session': session_payload(session, cache_data)}, status=201)


class SessionCodeView(APIView):
    """The current 6-digit code and the QR's check-in link (poll every few seconds)."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error
        if not session.qr_token:
            return error_response('This session has no check-in code.', status=400, code='no_code')
        cache_data = redis_service.get_session_cache(str(session.id))
        if not session_is_live(session, cache_data):
            if session.is_active:
                try:
                    finalize_session(session)
                except redis_service.SessionBusy:
                    pass  # the next status poll saves it
            return session_ended_response()
        code, expires_in = codes.current_code(session.qr_token)
        return Response({
            'success': True,
            'code': code,
            'check_in_url': codes.check_in_url(session.id, code),
            'expires_in': expires_in,
            'period': codes.PERIOD,
        })


class SessionStatusView(APIView):
    """Live: who checked in and who not. Ended: saves the session (once) and says so."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error

        cache_data = redis_service.get_session_cache(str(session.id))
        if not session_is_live(session, cache_data):
            # Timer ran out or session was stopped: make sure it is saved
            try:
                saved = finalize_session(session)
            except redis_service.SessionBusy:
                return busy_response()
            if saved is None:
                return cancelled_response()
            return Response({
                'success': True,
                'active': False,
                'session_id': str(session.id),
                'saved': not saved.is_active,
                'total_present': AttendanceLog.objects.filter(
                    session=session, status=AttendanceLog.Status.PRESENT,
                ).count(),
            })

        # Live data missing although the timer still runs: nobody is checked in yet
        submissions = cache_data.get('submissions', {}) if cache_data else {}
        memberships = list(
            enrolled_memberships(session.course_info, session.date)
            .select_related('student__user').order_by('student__student_id')
        )
        checked_in, not_checked_in = [], []
        for membership in memberships:
            student = membership.student
            submission = submissions.get(str(student.student_id))
            if submission is None:
                not_checked_in.append(student_brief(student))
                continue
            checked_in.append({
                **student_brief(student),
                'time': local_datetime(submission['time']).isoformat() if submission.get('time') else None,
                'method': submission.get('method') or 'QR',
                '_at': submission.get('time') or 0,
            })
        checked_in.sort(key=lambda row: row.pop('_at'), reverse=True)  # newest first
        return Response({
            'success': True,
            'active': True,
            'session': {
                'id': str(session.id),
                'delivery': session.delivery,
                'ends_at': session_ends_at(session, cache_data).isoformat(),
                'time_left': session_time_left(session, cache_data),
                'total': len(memberships),
                'checked_in': checked_in,
                'not_checked_in': not_checked_in,
            },
        })


class ExtendSessionView(APIView):
    """Adds minutes to a live session (30 minutes in total at most)."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error
        serializer = ExtendSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        seconds = serializer.validated_data['minutes'] * 60

        try:
            with transaction.atomic():
                locked = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
                if locked is None:
                    return cancelled_response()
                cache_data = redis_service.get_session_cache(str(locked.id))
                if not session_is_live(locked, cache_data):
                    return session_ended_response()
                total = locked.duration_seconds + seconds
                if total > MAX_SESSION_SECONDS:
                    room = (MAX_SESSION_SECONDS - locked.duration_seconds) // 60
                    message = 'A session can last at most 30 minutes.'
                    if room > 0:
                        message += f' You can add up to {room} more minute{"s" if room != 1 else ""}.'
                    return error_response(message, status=400, code='too_long')
                if cache_data is not None and redis_service.extend_session(str(locked.id), seconds) is None:
                    return session_ended_response()
                locked.duration_seconds = total
                locked.save(update_fields=['duration_seconds'])
        except redis_service.SessionBusy:
            return busy_response()

        cache_data = redis_service.get_session_cache(str(locked.id))
        return Response({
            'success': True,
            'ends_at': session_ends_at(locked, cache_data).isoformat(),
            'time_left': session_time_left(locked, cache_data),
        })


class LiveMarkView(APIView):
    """Teacher checks a student in during the live session (method TEACHER, saved with it)."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error
        serializer = LiveMarkSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)

        student = StudentProfile.objects.select_related('user').filter(
            id=serializer.validated_data['profile_id'], user__deleted=False,
        ).first()
        # Anyone the session will save (enrolled on its date): also a student removed today after
        # a class, who is still counted today (left_at = tomorrow) but can't check in themselves.
        enrolled = student is not None and enrolled_memberships(session.course_info, session.date).filter(
            student=student,
        ).exists()
        if not enrolled:
            return error_response('This student is not in this course.', status=400, code='not_enrolled')
        if not session_is_live(session, redis_service.get_session_cache(str(session.id))):
            return session_ended_response('This session has ended. Change attendance in the course history.')

        try:
            result = redis_service.add_submission(
                session_id=str(session.id), student_int_id=student.student_id,
                student_name=person_name(student.user), method=AttendanceLog.Method.TEACHER,
                profile_id=str(student.id),
            )
        except redis_service.SessionBusy:
            return busy_response()
        if result == redis_service.CLOSED:
            return session_ended_response('This session has ended. Change attendance in the course history.')
        if result == redis_service.ALREADY_CHECKED_IN:
            return error_response(
                f'{person_name(student.user)} is already checked in.', status=409, code='already_checked_in',
            )
        return Response({
            'success': True,
            'message': f'{person_name(student.user)} is checked in.',
            'student': {
                **student_brief(student),
                'time': timezone.localtime().isoformat(),
                'method': AttendanceLog.Method.TEACHER,
            },
        })


class StopSessionView(APIView):
    """Teacher stops session — commits all Redis submissions to DB."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error

        # Also fine when the timer or a status poll already saved it.
        try:
            saved = finalize_session(session)
        except redis_service.SessionBusy:
            return busy_response()
        if saved is None:
            return cancelled_response()

        present_count = AttendanceLog.objects.filter(
            session=session, status=AttendanceLog.Status.PRESENT
        ).count()

        return Response({
            'success': True,
            'message': 'Session stopped and attendance saved.',
            'session_id': str(session.id),
            'total_present': present_count,
        })


class CancelSessionView(APIView):
    """Discards a live session and its check-ins; nothing is saved."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        session, error = managed_session(request, uuid)
        if error:
            return error
        result = cancel_session(session)
        if result == 'gone':
            return cancelled_response()
        if result == 'saved':
            return error_response(
                'This session was already saved. Delete its date in the course history instead.',
                status=409, code='session_saved',
            )
        return Response({'success': True, 'message': 'Session cancelled. Nothing was saved.'})


class CheckInView(APIView):
    """
    Student checks in with the code: {code, session_id?, device_id}. With session_id (the
    QR link) the method is QR; without it (typed code) the student's live session whose
    current code matches is used and the method is CODE.
    """
    permission_classes = [IsStudent]
    throttle_classes = [CheckInThrottle]

    def post(self, request):
        serializer = CheckInSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        code = normalize_code(data['code'])

        student = StudentProfile.objects.select_related('user').filter(
            user=request.user, user__deleted=False,
        ).first()
        if student is None:
            return error_response('You are not in this course.', status=403, code='not_enrolled')

        if data.get('session_id'):
            method = AttendanceLog.Method.QR
            session = AttendanceSession.objects.select_related('course_info__course').filter(
                id=data['session_id'], mode=AttendanceSession.Mode.QR_ONLINE,
                course_info__deleted=False, course_info__course__deleted=False,
                course_info__semester__deleted=False,
            ).first()
            if session is None:  # e.g. cancelled
                return session_ended_response()
            if not can_join_live(student, session):
                return error_response('You are not in this course.', status=403, code='not_enrolled')
            if not session_is_live(session, redis_service.get_session_cache(str(session.id))):
                return session_ended_response()
            if not CODE_PATTERN.match(code) or not codes.code_matches(session.qr_token, code):
                return code_invalid_response()
        else:
            method = AttendanceLog.Method.CODE
            session = None
            if CODE_PATTERN.match(code):
                session = self.find_by_code(student, code)
            if session is None:
                return code_invalid_response()

        try:
            result = redis_service.add_submission(
                session_id=str(session.id), student_int_id=student.student_id,
                student_name=person_name(student.user), method=method,
                device_id=data['device_id'], profile_id=str(student.id),
            )
        except redis_service.SessionBusy:
            return busy_response()
        if result == redis_service.CLOSED:
            return session_ended_response()
        if result == redis_service.ALREADY_CHECKED_IN:
            return error_response('You are already checked in.', status=409, code='already_checked_in')
        if result == redis_service.DEVICE_USED:
            return error_response(
                'This device was already used to check in another student in this session.',
                status=409, code='device_used',
            )

        course = session.course_info.course
        return Response({
            'success': True,
            'message': f'Checked in to {course.code}.',
            'course': {'code': course.code, 'title': course.title},
            'time': timezone.localtime().isoformat(),
        })

    @staticmethod
    def find_by_code(student, code):
        """The student's live session (own current courses) whose current code is `code`."""
        classroom_ids = StudentClassroom.objects.filter(student=student).current().values('classroom_id')
        sessions = AttendanceSession.objects.filter(
            is_active=True, mode=AttendanceSession.Mode.QR_ONLINE, qr_token__isnull=False,
            course_info__classroom_id__in=classroom_ids,
            course_info__deleted=False, course_info__course__deleted=False, course_info__semester__deleted=False,
        ).select_related('course_info__course')
        for session, _ in live_sessions(sessions):
            if codes.code_matches(session.qr_token, code) and can_join_live(student, session):
                return session
        return None


def code_invalid_response():
    return error_response(
        'This code is wrong or has expired. Check the newest code.', status=400, code='code_invalid',
    )


class CourseAttendanceHistoryView(APIView):
    """
    The course's classes by date (newest first): each day's saved sessions and one row per
    student (one date = one class). ?date=YYYY-MM-DD and ?student_id=<profile id> narrow it.
    """
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False, course__deleted=False, semester__deleted=False)
        if not can_manage_course(request.user, ci):
            return not_your_course_response()

        finalize_expired_sessions(ci)
        logs = AttendanceLog.objects.filter(
            course_info=ci, student__user__deleted=False,
        ).select_related('student__user', 'changed_by')
        sessions = AttendanceSession.objects.filter(course_info=ci, is_active=False).order_by('started_at')

        date_filter = (request.query_params.get('date') or '').strip()
        if date_filter:
            day = parse_day(date_filter)
            if day is None:
                return validation_error_response({'date': ['Use a YYYY-MM-DD date.']})
            logs = logs.filter(date=day)
            sessions = sessions.filter(date=day)

        student_filter = (request.query_params.get('student_id') or '').strip()
        if student_filter:
            try:
                logs = logs.filter(student_id=uuid_lib.UUID(student_filter))
            except ValueError:
                return validation_error_response({'student_id': ['This student was not found.']})

        by_day = defaultdict(lambda: defaultdict(list))
        for log in logs:
            by_day[log.date][log.student_id].append(log)
        sessions_by_day = defaultdict(list)
        for session in sessions:
            sessions_by_day[session.date].append(
                {'session_id': str(session.id), 'delivery': session.delivery, 'mode': session.mode}
            )

        history = []
        for day in sorted(by_day, reverse=True):
            rows = []
            for student_logs in by_day[day].values():
                status, deciding = day_status(student_logs)
                rows.append({**student_brief(deciding.student), 'status': status, **log_details(deciding)})
            rows.sort(key=lambda row: row['student_id'])
            history.append({'date': day.isoformat(), 'sessions': sessions_by_day.get(day, []), 'logs': rows})

        return Response({'success': True, 'course_info_id': str(ci.id), 'history': history})
