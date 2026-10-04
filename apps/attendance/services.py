"""
Business logic layer for attendance — keeps views thin.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AttendanceSession, AttendanceLog
from apps.academic.models import StudentClassroom
from apps.attendance import redis_service

logger = logging.getLogger(__name__)

# Which AttendanceLog.Source a session's check-ins are saved with.
SOURCE_FOR_MODE = {
    AttendanceSession.Mode.FINGERPRINT: AttendanceLog.Source.HARDWARE,
    AttendanceSession.Mode.QR_ONLINE: AttendanceLog.Source.QR_ONLINE,
    AttendanceSession.Mode.QR_OFFLINE: AttendanceLog.Source.QR_OFFLINE,
    AttendanceSession.Mode.FACE: AttendanceLog.Source.FACE,
}


def commit_session_to_db(session: AttendanceSession, submissions: dict):
    """
    Called when a session is stopped.
    Creates PRESENT logs for submitted students,
    ABSENT logs for everyone else in the classroom.
    Students who already have a log for this session (e.g. a teacher's
    manual mark) keep it.
    submissions: { "<student_int_id>": {"name": str, "mac": str} }
    """
    enrolled = StudentClassroom.objects.filter(
        classroom=session.course_info.classroom
    ).select_related('student')

    submitted_ids = {int(k) for k in submissions.keys()}
    now_time = timezone.localtime().time()
    source = SOURCE_FOR_MODE.get(session.mode, AttendanceLog.Source.MANUAL)

    logs_to_create = []
    for membership in enrolled:
        student = membership.student
        is_present = student.student_id in submitted_ids
        logs_to_create.append(AttendanceLog(
            session=session,
            course_info=session.course_info,
            student=student,
            date=session.date,
            time=now_time,
            status=AttendanceLog.Status.PRESENT if is_present else AttendanceLog.Status.ABSENT,
            source=source,
        ))

    AttendanceLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)


def finalize_session(session: AttendanceSession):
    """
    Ends a session and saves its check-ins to the database. Safe to call
    more than once (stop button, timer and status poll can race): only the
    first call saves anything.
    """
    with transaction.atomic():
        session = AttendanceSession.objects.select_for_update().get(pk=session.pk)
        if not session.is_active:
            return session
        session_id = str(session.id)
        submissions = redis_service.close_session(session_id)
        session.is_active = False
        session.ended_at = timezone.now()
        session.save(update_fields=['is_active', 'ended_at'])
        commit_session_to_db(session, submissions)
        transaction.on_commit(lambda: redis_service.delete_session_cache(session_id))
    return session


def session_has_ended(session: AttendanceSession, cache_data: dict | None) -> bool:
    """
    True once check-ins are closed. Without live data (e.g. Redis lost it),
    trust the database timer instead of treating the session as over.
    """
    if cache_data is not None:
        return not redis_service.is_open(cache_data)
    return timezone.now() >= session.started_at + timedelta(seconds=session.duration_seconds)


def finalize_ended_sessions(sessions):
    """
    Best-effort background save of active sessions whose timer has run out.
    A failure (e.g. Redis unreachable) leaves the session for a later request.
    """
    for session in sessions.filter(is_active=True):
        try:
            if session_has_ended(session, redis_service.get_session_cache(str(session.id))):
                finalize_session(session)
        except Exception:
            logger.exception('Could not save ended attendance session %s', session.id)


def finalize_expired_sessions(course_info):
    """Saves any session of this course (instance or id) whose timer has run out."""
    finalize_ended_sessions(AttendanceSession.objects.filter(course_info=course_info))


def get_student_attendance_summary(student_profile):
    """
    Returns attendance grouped by semester for the student dashboard.
    """
    from apps.academic.models import Semester

    semesters = Semester.objects.filter(
        classrooms__memberships__student=student_profile,
        deleted=False
    ).distinct().prefetch_related('course_infos__course', 'course_infos__teacher__user')

    result = []
    for sem in semesters:
        courses_data = []
        for ci in sem.course_infos.filter(deleted=False):
            logs = AttendanceLog.objects.filter(
                course_info=ci,
                student=student_profile,
            )

            # One class = one date: present if present in any session that day.
            dates = sorted(set(logs.values_list('date', flat=True)))
            present_dates = set(
                logs.filter(status=AttendanceLog.Status.PRESENT).values_list('date', flat=True)
            )
            total = len(dates)
            present = len(present_dates)
            percentage = round((present / total * 100), 2) if total > 0 else 0.0

            history = [
                {'date': str(day), 'present': day in present_dates}
                for day in dates
            ]

            courses_data.append({
                'id': str(ci.id),
                'course': {
                    'id': str(ci.course.id),
                    'code': ci.course.code,
                    'title': ci.course.title,
                    'credits': ci.course.credits,
                },
                'teacher': {
                    'userName': ci.teacher.user.get_full_name() if ci.teacher else None,
                    'email': ci.teacher.user.email if ci.teacher else None,
                },
                'totalClasses': total,
                'presentCount': present,
                'percentage': percentage,
                'history': history,
            })

        result.append({
            'id': str(sem.id),
            'level': sem.level,
            'semester': sem.semester,
            'start_date': str(sem.start_date) if sem.start_date else None,
            'end_date': str(sem.end_date) if sem.end_date else None,
            'is_active': sem.is_active,
            'courses': courses_data,
        })

    return result
