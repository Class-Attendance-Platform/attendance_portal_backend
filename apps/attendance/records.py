from collections import defaultdict

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.academic.models import StudentClassroom
from apps.academic.serializers import iso_date, person_name, semester_brief
from apps.academic.stats import round_percent
from apps.attendance.models import AttendanceLog

PRESENT = AttendanceLog.Status.PRESENT
ABSENT = AttendanceLog.Status.ABSENT

def parse_day(text):
    try:
        return parse_date(str(text).strip()) if len(str(text).strip()) <= 10 else None
    except ValueError:
        return None

def iso_datetime(value):
    return timezone.localtime(value).isoformat() if value else None

def percent(attended, held):
    return round_percent(attended * 100 / held) if held else None

def below_min(attended, held) -> bool:
    return held > 0 and attended * 100 < settings.ATTENDANCE_MIN_PERCENT * held

def classes_needed(attended, held, minimum=None):
    minimum = settings.ATTENDANCE_MIN_PERCENT if minimum is None else minimum
    if attended * 100 >= minimum * held:
        return 0
    if minimum >= 100:
        return None
    return -(-(minimum * held - 100 * attended) // (100 - minimum))

def day_status(logs):
    def first(candidates):
        return min(candidates, key=lambda log: (log.time is None, log.time or 0))

    present = [log for log in logs if log.status == PRESENT]
    if present:
        return PRESENT, first(present)
    changed = [log for log in logs if log.changed_at is not None]
    if changed:
        return ABSENT, max(changed, key=lambda log: log.changed_at)
    return ABSENT, first(logs)

def shown_method(log):
    if log.status == PRESENT:
        return log.method or (AttendanceLog.Method.TEACHER if log.is_modified_by_teacher else None)
    return log.method if log.method == AttendanceLog.Method.TEACHER else None

def log_details(log) -> dict:
    if log is None:
        return {'method': None, 'changed_by': None, 'changed_at': None}
    return {
        'method': shown_method(log),
        'changed_by': person_name(log.changed_by) if log.changed_by_id and log.changed_by else None,
        'changed_at': iso_datetime(log.changed_at),
    }

def absent_reason(log):
    if log is None:
        return 'not_checked_in'
    if log.changed_at is not None or log.changed_by_id is not None:
        return 'changed'
    if log.source == AttendanceLog.Source.FACE:
        return 'class_photo'
    if log.method == AttendanceLog.Method.TEACHER or log.source == AttendanceLog.Source.MANUAL:
        return 'roll_call'
    return 'not_checked_in'

def class_dates(course_info_ids) -> dict:
    dates = defaultdict(set)
    rows = AttendanceLog.objects.filter(course_info_id__in=list(course_info_ids)).order_by()
    for ci_id, day in rows.values_list('course_info_id', 'date').distinct():
        dates[ci_id].add(day)
    return {ci_id: sorted(days, reverse=True) for ci_id, days in dates.items()}

def logs_by_day(course_info_ids, student) -> dict:
    result = defaultdict(lambda: defaultdict(list))
    logs = AttendanceLog.objects.filter(
        course_info_id__in=list(course_info_ids), student=student,
    ).select_related('changed_by')
    for log in logs:
        result[log.course_info_id][log.date].append(log)
    return result

def student_days(dates, membership, day_logs, reasons=False) -> list:
    rows = []
    for day in dates:
        logs = day_logs.get(day)
        if not membership.counts_on(day, bool(logs)):
            status, deciding = None, None
        else:
            status, deciding = day_status(logs) if logs else (ABSENT, None)
        row = {'date': iso_date(day), 'status': status, **log_details(deciding)}
        if reasons:
            row['absent_reason'] = absent_reason(deciding) if status == ABSENT else None
        rows.append(row)
    return rows

def count_days(days):
    held = [d for d in days if d['status'] is not None]
    return sum(d['status'] == PRESENT for d in held), len(held)

def course_days(course_info, membership, reasons=False) -> list:
    dates = class_dates([course_info.id]).get(course_info.id, [])
    day_logs = logs_by_day([course_info.id], membership.student)[course_info.id]
    return student_days(dates, membership, day_logs, reasons=reasons)

def get_student_attendance_summary(student_profile):
    memberships = (
        StudentClassroom.objects.filter(
            student=student_profile, classroom__deleted=False, classroom__semester__deleted=False,
        ).select_related('classroom__semester')
    )
    by_semester = {}
    for membership in sorted(memberships, key=lambda m: m.left_at is not None):
        by_semester.setdefault(membership.classroom.semester_id, membership)
    ordered = sorted(
        by_semester.values(),
        key=lambda m: (*m.classroom.semester.sort_key(), m.classroom.semester.session),
    )

    result = []
    for membership in ordered:
        sem = membership.classroom.semester
        course_infos = list(
            membership.classroom.course_infos.filter(deleted=False, course__deleted=False)
            .select_related('course', 'teacher__user').order_by('course__code')
        )
        ids = [ci.id for ci in course_infos]
        dates = class_dates(ids)
        logs = logs_by_day(ids, student_profile)

        courses_data = []
        total_attended = total_held = 0
        for ci in course_infos:
            days = student_days(dates.get(ci.id, []), membership, logs[ci.id])
            attended, held = count_days(days)
            total_attended += attended
            total_held += held
            courses_data.append({
                'id': str(ci.id),
                'course_info_id': str(ci.id),
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
                'held': held,
                'attended': attended,
                'percent': percent(attended, held),
                'below_min': below_min(attended, held),
                'classes_needed': classes_needed(attended, held),
                'totalClasses': held,
                'presentCount': attended,
                'percentage': attended * 10000 // held / 100 if held else 0.0,
                'history': [
                    {'date': d['date'], 'present': d['status'] == PRESENT}
                    for d in reversed(days) if d['status'] is not None
                ],
            })

        result.append({
            'id': str(sem.id),
            'level': sem.level,
            'semester': sem.semester,
            'session': sem.session,
            'label': sem.label,
            'start_date': iso_date(sem.start_date),
            'end_date': iso_date(sem.end_date),
            'is_active': sem.is_active,
            'overall_percent': percent(total_attended, total_held),
            'joined_at': iso_date(membership.joined_at),
            'left_at': iso_date(membership.left_at),
            'courses': courses_data,
        })

    return result

def student_course_detail(course_info, membership) -> dict:
    days = course_days(course_info, membership, reasons=True)
    attended, held = count_days(days)
    teacher = course_info.teacher
    return {
        'course': {
            'course_info_id': str(course_info.id),
            'code': course_info.course.code,
            'title': course_info.course.title,
            'credits': course_info.course.credits,
            'teacher_name': person_name(teacher.user) if teacher else None,
            'semester': {k: v for k, v in semester_brief(course_info.semester).items() if k != 'id'},
        },
        'joined_at': iso_date(membership.joined_at),
        'left_at': iso_date(membership.left_at),
        'attended': attended,
        'held': held,
        'percent': percent(attended, held),
        'classes_needed': classes_needed(attended, held),
        'days': [
            {'date': d['date'], 'status': d['status'], 'method': d['method'], 'changed': d['changed_at'] is not None,
             'absent_reason': d['absent_reason']}
            for d in days
        ],
    }
