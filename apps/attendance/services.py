"""
Business logic layer for attendance — keeps views thin.

Live sessions (API v2 section 5): `start` writes the Redis entry first, then the session
row; check-ins go to Redis (redis_service, locked read-modify-write); finalize_session()
saves PRESENT/ABSENT logs exactly once. Corrections and roll calls (section 6) change saved
logs and keep an AttendanceChange trail.
"""
import datetime as dt
import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AttendanceChange, AttendanceSession, AttendanceLog
from apps.academic.models import CourseInfo, StudentClassroom
from apps.attendance import codes, redis_service

logger = logging.getLogger(__name__)

MAX_SESSION_SECONDS = 30 * 60

# Which AttendanceLog.Source a session's check-ins are saved with.
SOURCE_FOR_MODE = {
    AttendanceSession.Mode.FINGERPRINT: AttendanceLog.Source.HARDWARE,
    AttendanceSession.Mode.QR_ONLINE: AttendanceLog.Source.QR_ONLINE,
    AttendanceSession.Mode.QR_OFFLINE: AttendanceLog.Source.QR_OFFLINE,
    AttendanceSession.Mode.FACE: AttendanceLog.Source.FACE,
}
# Method of a check-in saved before methods were recorded in the live data.
DEFAULT_METHOD_FOR_MODE = {
    AttendanceSession.Mode.FINGERPRINT: AttendanceLog.Method.FINGERPRINT,
    AttendanceSession.Mode.QR_ONLINE: AttendanceLog.Method.QR,
    AttendanceSession.Mode.QR_OFFLINE: AttendanceLog.Method.QR,
}


def enrolled_memberships(course_info, day):
    """Who is in the course's class group on `day` (deleted accounts left out)."""
    return StudentClassroom.objects.filter(
        classroom_id=course_info.classroom_id
    ).enrolled_on(day).active_accounts()


def can_join_live(student, session) -> bool:
    """A current member of the course's class group, enrolled on the session's date."""
    return enrolled_memberships(session.course_info, session.date).current().filter(student=student).exists()


def local_datetime(timestamp):
    """A unix timestamp as a local (Asia/Dhaka) date-time."""
    return timezone.localtime(dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc))


def commit_session_to_db(session: AttendanceSession, submissions: dict):
    """
    Called when a session is stopped.
    Creates PRESENT logs for submitted students (with their check-in method and time),
    ABSENT logs for everyone else in the classroom on the session's date
    (not former members, not students who joined later, not deleted accounts).
    Students who already have a log for this session keep it.
    submissions: { "<student_int_id>": {"name", "profile_id", "method", "device_id", "time"} }
    """
    enrolled = enrolled_memberships(session.course_info, session.date).select_related('student')

    now_time = timezone.localtime().time()
    source = SOURCE_FOR_MODE.get(session.mode, AttendanceLog.Source.MANUAL)
    default_method = DEFAULT_METHOD_FOR_MODE.get(session.mode, '')

    logs_to_create = []
    for membership in enrolled:
        student = membership.student
        submission = submissions.get(str(student.student_id))
        if submission is not None:
            checked_in = submission.get('time')
            logs_to_create.append(AttendanceLog(
                session=session, course_info=session.course_info, student=student, date=session.date,
                time=local_datetime(checked_in).time() if checked_in else now_time,
                status=AttendanceLog.Status.PRESENT, source=source,
                method=submission.get('method') or default_method,
            ))
        else:
            logs_to_create.append(AttendanceLog(
                session=session, course_info=session.course_info, student=student, date=session.date,
                time=now_time, status=AttendanceLog.Status.ABSENT, source=source,
            ))

    AttendanceLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)


def finalize_session(session: AttendanceSession):
    """
    Ends a session and saves its check-ins to the database. Safe to call
    more than once (stop button, timer and status poll can race): only the
    first call saves anything. Returns the session (None if it was cancelled meanwhile).
    """
    with transaction.atomic():
        session = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
        if session is None or not session.is_active:
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


def session_is_live(session: AttendanceSession, cache_data: dict | None) -> bool:
    return session.is_active and not session_has_ended(session, cache_data)


def session_ends_at(session: AttendanceSession, cache_data: dict | None):
    """When check-ins close (the live timer; the database timer without live data)."""
    if cache_data is not None:
        return local_datetime(cache_data['end_time'])
    return timezone.localtime(session.started_at + timedelta(seconds=session.duration_seconds))


def session_time_left(session: AttendanceSession, cache_data: dict | None) -> int:
    if not session.is_active:
        return 0
    return max(0, int((session_ends_at(session, cache_data) - timezone.now()).total_seconds()))


def session_payload(session: AttendanceSession, cache_data: dict | None) -> dict:
    """The live session as start and the teacher's live lookup return it."""
    return {
        'id': str(session.id),
        'course_info_id': str(session.course_info_id),
        'delivery': session.delivery,
        'date': session.date.isoformat(),
        'started_at': timezone.localtime(session.started_at).isoformat(),
        'ends_at': session_ends_at(session, cache_data).isoformat(),
        'time_left': session_time_left(session, cache_data),
        'code_period': codes.PERIOD,
    }


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


def safe_session_cache(session) -> dict | None:
    """The live data, or None when Redis cannot be reached (then the database timer counts)."""
    try:
        return redis_service.get_session_cache(str(session.id))
    except Exception:
        logger.exception('Could not read live data of session %s', session.id)
        return None


def live_sessions(sessions) -> list:
    """[(session, live data)] of the sessions still taking check-ins (ended ones are saved first)."""
    finalize_ended_sessions(sessions)
    result = []
    for session in sessions.filter(is_active=True):
        cache_data = safe_session_cache(session)
        if session_is_live(session, cache_data):
            result.append((session, cache_data))
    return result


def live_session(course_info):
    """(session, live data) of the course's live session, or (None, None)."""
    found = live_sessions(AttendanceSession.objects.filter(course_info=course_info).select_related('course_info'))
    return found[0] if found else (None, None)


def cancel_session(session: AttendanceSession) -> str:
    """
    Discards a session that has not been saved: the row and its check-ins go, nothing is
    saved. Returns 'cancelled', 'saved' (already saved: nothing changed) or 'gone'.
    """
    with transaction.atomic():
        locked = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
        if locked is None:
            return 'gone'
        if not locked.is_active:
            return 'saved'
        session_id = str(locked.id)
        locked.delete()
        transaction.on_commit(lambda: redis_service.discard_session(session_id))
    return 'cancelled'


# ── Corrections and roll calls (section 6) ────────────────────────────────────

def _change(log, status, user, now):
    change = AttendanceChange(log=log, old_status=log.status, new_status=status, changed_by=user, changed_at=now)
    log.status = status
    log.changed_by = user
    log.changed_at = now
    log.is_modified_by_teacher = True
    return change


def _set_status(course_info, student, day, status, logs, user, now, record_new):
    """
    Makes the student's day status `status`. Existing logs keep their method and source;
    only those with another status change. Returns 'created', 'changed' or None.
    """
    if not logs:
        log = AttendanceLog.objects.create(
            course_info=course_info, student=student, date=day, time=timezone.localtime(now).time(),
            status=status, source=AttendanceLog.Source.MANUAL, method=AttendanceLog.Method.TEACHER,
            is_modified_by_teacher=True,
            changed_by=user if record_new else None, changed_at=now if record_new else None,
        )
        if record_new:
            AttendanceChange.objects.create(log=log, old_status='', new_status=status, changed_by=user, changed_at=now)
        return 'created'
    present = any(log.status == AttendanceLog.Status.PRESENT for log in logs)
    if present == (status == AttendanceLog.Status.PRESENT):
        return None
    to_change = [log for log in logs if log.status != status]
    changes = [_change(log, status, user, now) for log in to_change]
    AttendanceLog.objects.bulk_update(to_change, ['status', 'changed_by', 'changed_at', 'is_modified_by_teacher'])
    AttendanceChange.objects.bulk_create(changes)
    return 'changed'


def _lock_course(course_info):
    """Serialises corrections of one course (no double logs from double taps)."""
    CourseInfo.objects.select_for_update().filter(pk=course_info.pk).first()


def correct_attendance(course_info, student, day, status, user) -> bool:
    """
    One student's status on a date that has a class (PUT .../attendance/). Records who
    changed it and when, with an AttendanceChange row. Returns False if it was already so.
    """
    now = timezone.now()
    with transaction.atomic():
        _lock_course(course_info)
        logs = list(AttendanceLog.objects.select_for_update().filter(
            course_info=course_info, student=student, date=day,
        ))
        return _set_status(course_info, student, day, status, logs, user, now, record_new=True) is not None


def roll_call(course_info, day, present_ids, user) -> dict:
    """
    Saves a roll call for a date (creates the class if missing). For each student enrolled
    on that date: a log is created if missing; an existing one changes only if its status
    differs (recorded like a correction). Other methods are never re-labelled.
    """
    present_ids = {str(i) for i in present_ids}
    now = timezone.now()
    counts = {'present': 0, 'absent': 0, 'changed': 0}
    with transaction.atomic():
        _lock_course(course_info)
        logs = defaultdict(list)
        for log in AttendanceLog.objects.select_for_update().filter(course_info=course_info, date=day):
            logs[log.student_id].append(log)
        for membership in enrolled_memberships(course_info, day).select_related('student'):
            student = membership.student
            is_present = str(student.id) in present_ids
            counts['present' if is_present else 'absent'] += 1
            status = AttendanceLog.Status.PRESENT if is_present else AttendanceLog.Status.ABSENT
            if _set_status(course_info, student, day, status, logs[student.id], user, now, record_new=False) == 'changed':
                counts['changed'] += 1
    return counts
