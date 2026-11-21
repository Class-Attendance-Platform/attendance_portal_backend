import datetime as dt
import hashlib
import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AttendanceChange, AttendanceSession, AttendanceLog
from apps.academic.models import CourseInfo, StudentClassroom
from apps.attendance import codes
from apps.attendance.records import day_status
from apps.users.models import StudentProfile

logger = logging.getLogger(__name__)

MAX_SESSION_SECONDS = 30 * 60

SOURCE_FOR_MODE = {
    AttendanceSession.Mode.FINGERPRINT: AttendanceLog.Source.HARDWARE,
    AttendanceSession.Mode.QR_ONLINE: AttendanceLog.Source.QR_ONLINE,
    AttendanceSession.Mode.QR_OFFLINE: AttendanceLog.Source.QR_OFFLINE,
    AttendanceSession.Mode.FACE: AttendanceLog.Source.FACE,
}
DEFAULT_METHOD_FOR_MODE = {
    AttendanceSession.Mode.FINGERPRINT: AttendanceLog.Method.FINGERPRINT,
    AttendanceSession.Mode.QR_ONLINE: AttendanceLog.Method.QR,
    AttendanceSession.Mode.QR_OFFLINE: AttendanceLog.Method.QR,
}

ADDED = 'added'
CLOSED = 'closed'
ALREADY_CHECKED_IN = 'already_checked_in'
DEVICE_USED = 'device_used'

def enrolled_memberships(course_info, day):
    return StudentClassroom.objects.filter(
        classroom_id=course_info.classroom_id
    ).enrolled_on(day).active_accounts()

def can_join_live(student, session) -> bool:
    return enrolled_memberships(session.course_info, session.date).current().filter(student=student).exists()

def local_datetime(timestamp):
    if isinstance(timestamp, (int, float)):
        return timezone.localtime(dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc))
    return timezone.localtime(timestamp)

def record_check_in(session: AttendanceSession, student, method: str, device_id: str = '', now=None) -> str:
    now = timezone.now() if now is None else now
    with transaction.atomic():
        locked_session = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
        if locked_session is None or not session_is_live(locked_session):
            return CLOSED

        if device_id:
            if AttendanceLog.objects.filter(session=locked_session, device_id=device_id).exclude(student=student).exists():
                return DEVICE_USED

        existing = AttendanceLog.objects.filter(session=locked_session, student=student).first()
        if existing is not None and existing.status == AttendanceLog.Status.PRESENT:
            return ALREADY_CHECKED_IN

        source = SOURCE_FOR_MODE.get(locked_session.mode, AttendanceLog.Source.MANUAL)
        now_time = timezone.localtime(now).time()
        if existing is not None:
            existing.status = AttendanceLog.Status.PRESENT
            existing.source = source
            existing.method = method
            existing.device_id = device_id or ''
            existing.time = now_time
            existing.save(update_fields=['status', 'source', 'method', 'device_id', 'time'])
        else:
            AttendanceLog.objects.create(
                session=locked_session,
                course_info=locked_session.course_info,
                student=student,
                date=locked_session.date,
                time=now_time,
                status=AttendanceLog.Status.PRESENT,
                source=source,
                method=method,
                device_id=device_id or '',
            )
        return ADDED

def finalize_session(session: AttendanceSession):
    with transaction.atomic():
        locked = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
        if locked is None or not locked.is_active:
            return locked
        locked.is_active = False
        locked.ended_at = timezone.now()
        locked.save(update_fields=['is_active', 'ended_at'])

        enrolled = enrolled_memberships(locked.course_info, locked.date).select_related('student')
        existing_student_ids = set(
            AttendanceLog.objects.filter(session=locked).values_list('student_id', flat=True)
        )

        now_time = timezone.localtime().time()
        source = SOURCE_FOR_MODE.get(locked.mode, AttendanceLog.Source.MANUAL)

        absent_logs = [
            AttendanceLog(
                session=locked,
                course_info=locked.course_info,
                student=membership.student,
                date=locked.date,
                time=now_time,
                status=AttendanceLog.Status.ABSENT,
                source=source,
            )
            for membership in enrolled
            if membership.student_id not in existing_student_ids
        ]
        if absent_logs:
            AttendanceLog.objects.bulk_create(absent_logs, ignore_conflicts=True)
    return locked

def commit_session_to_db(session: AttendanceSession, submissions: dict):
    source = SOURCE_FOR_MODE.get(session.mode, AttendanceLog.Source.QR_ONLINE)
    students_by_id = {s.student_id: s for s in StudentProfile.objects.all()}
    now_time = timezone.localtime().time()

    for s_id_str, data in submissions.items():
        try:
            s_id = int(s_id_str)
        except (ValueError, TypeError):
            continue
        student = students_by_id.get(s_id)
        if not student:
            continue
        sub_time = data.get('time')
        if sub_time:
            log_time = local_datetime(sub_time).time()
        else:
            log_time = now_time
        AttendanceLog.objects.update_or_create(
            session=session,
            student=student,
            defaults={
                'course_info': session.course_info,
                'date': session.date,
                'time': log_time,
                'status': AttendanceLog.Status.PRESENT,
                'source': source,
                'method': data.get('method', AttendanceLog.Method.QR),
                'device_id': data.get('device_id', ''),
            }
        )
    finalize_session(session)

def session_has_ended(session: AttendanceSession, cache_data=None) -> bool:
    if not session.is_active:
        return True
    return timezone.now() >= session.started_at + timedelta(seconds=session.duration_seconds)

def session_is_live(session: AttendanceSession, cache_data=None) -> bool:
    return session.is_active and not session_has_ended(session)

def session_ends_at(session: AttendanceSession, cache_data=None):
    return timezone.localtime(session.started_at + timedelta(seconds=session.duration_seconds))

def session_time_left(session: AttendanceSession, cache_data=None) -> int:
    if not session.is_active:
        return 0
    return max(0, int((session_ends_at(session) - timezone.now()).total_seconds()))

def session_payload(session: AttendanceSession, cache_data=None) -> dict:
    return {
        'id': str(session.id),
        'course_info_id': str(session.course_info_id),
        'delivery': session.delivery,
        'date': session.date.isoformat(),
        'started_at': timezone.localtime(session.started_at).isoformat(),
        'ends_at': session_ends_at(session).isoformat(),
        'time_left': session_time_left(session),
        'code_period': codes.PERIOD,
    }

def finalize_ended_sessions(sessions):
    for session in sessions.filter(is_active=True):
        try:
            if session_has_ended(session):
                finalize_session(session)
        except Exception:
            logger.exception('Could not finalize ended attendance session %s', session.id)

def finalize_expired_sessions(course_info):
    finalize_ended_sessions(AttendanceSession.objects.filter(course_info=course_info))

def live_sessions(sessions) -> list:
    finalize_ended_sessions(sessions)
    result = []
    for session in sessions.filter(is_active=True):
        if session_is_live(session):
            result.append((session, None))
    return result

def live_session(course_info):
    found = live_sessions(AttendanceSession.objects.filter(course_info=course_info).select_related('course_info'))
    return found[0] if found else (None, None)

def cancel_session(session: AttendanceSession) -> str:
    with transaction.atomic():
        locked = AttendanceSession.objects.select_for_update().filter(pk=session.pk).first()
        if locked is None:
            return 'gone'
        if not locked.is_active:
            return 'saved'
        locked.delete()
    return 'cancelled'

def _change(log, status, user, now):
    change = AttendanceChange(log=log, old_status=log.status, new_status=status, changed_by=user, changed_at=now)
    log.status = status
    log.changed_by = user
    log.changed_at = now
    log.is_modified_by_teacher = True
    return change

def _set_status(course_info, student, day, status, logs, user, now, record_new):
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
    CourseInfo.objects.select_for_update().filter(pk=course_info.pk).order_by().first()

def correct_attendance(course_info, student, day, status, user) -> bool:
    now = timezone.now()
    with transaction.atomic():
        _lock_course(course_info)
        logs = list(AttendanceLog.objects.select_for_update().filter(
            course_info=course_info, student=student, date=day,
        ))
        return _set_status(course_info, student, day, status, logs, user, now, record_new=True) is not None

class DateChanged(Exception):
    pass

def roll_call_list(course_info, day, logs=None) -> list:
    if logs is None:
        logs = defaultdict(list)
        for log in AttendanceLog.objects.filter(course_info=course_info, date=day):
            logs[log.student_id].append(log)
    rows = []
    for membership in enrolled_memberships(course_info, day).select_related('student__user').order_by(
        'student__student_id',
    ):
        found = logs.get(membership.student_id)
        rows.append((membership, day_status(found)[0] if found else None))
    return rows

def roll_call_version(rows) -> str:
    text = '|'.join(f'{m.student_id}:{status or "-"}' for m, status in rows)
    return hashlib.sha256(text.encode()).hexdigest()[:16]

def roll_call(course_info, day, present_ids, user, expected_version=None, absent_ids=None) -> dict:
    present_ids = {str(i) for i in present_ids}
    absent_ids = None if absent_ids is None else {str(i) for i in absent_ids}
    now = timezone.now()
    counts = {'present': 0, 'absent': 0, 'unmarked': 0, 'changed': 0}
    with transaction.atomic():
        _lock_course(course_info)
        logs = defaultdict(list)
        for log in AttendanceLog.objects.select_for_update().filter(course_info=course_info, date=day):
            logs[log.student_id].append(log)
        if expected_version is not None and roll_call_version(roll_call_list(course_info, day, logs)) != expected_version:
            raise DateChanged()
        for membership in enrolled_memberships(course_info, day).select_related('student'):
            student = membership.student
            student_logs = logs[student.id]
            if str(student.id) in present_ids:
                status = AttendanceLog.Status.PRESENT
            elif absent_ids is None or str(student.id) in absent_ids:
                status = AttendanceLog.Status.ABSENT
            else:
                if not student_logs:
                    counts['unmarked'] += 1
                elif day_status(student_logs)[0] == AttendanceLog.Status.PRESENT:
                    counts['present'] += 1
                else:
                    counts['absent'] += 1
                continue
            counts['present' if status == AttendanceLog.Status.PRESENT else 'absent'] += 1
            if _set_status(course_info, student, day, status, student_logs, user, now, record_new=False) == 'changed':
                counts['changed'] += 1
    return counts
