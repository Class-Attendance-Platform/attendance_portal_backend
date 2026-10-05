"""
Reading attendance by day (API v2 sections 6 and 7).

One date = one class: a student is present on a date if any of that day's logs marks them
present (LATE, old data only, counts as not present). A course's class dates are the dates
that have any log. For a student only the class dates inside their membership count
(joined_at <= date < left_at; on the join day only with a log for them, since a class held
before they were added that day has none: StudentClassroom.counts_on): those are `held`; the
others show status null ("not enrolled"). A class date inside the membership without a log for
them counts as absent.
"""
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
    """A YYYY-MM-DD date (leading zeros optional), or None if it is not one."""
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
    """
    Classes in a row to attend to reach the minimum percent (0 if already there; None if
    it can never be reached, i.e. a 100% minimum after a missed class).
    """
    minimum = settings.ATTENDANCE_MIN_PERCENT if minimum is None else minimum
    if attended * 100 >= minimum * held:
        return 0
    if minimum >= 100:
        return None
    return -(-(minimum * held - 100 * attended) // (100 - minimum))  # ceiling division


def day_status(logs):
    """(status, deciding log) for one student's logs on one date (logs not empty)."""
    ordered = sorted(logs, key=lambda log: (log.status != PRESENT, log.time is None, log.time or 0))
    deciding = ordered[0]
    return (PRESENT if deciding.status == PRESENT else ABSENT), deciding


def shown_method(log):
    """
    How the student was marked present (section 6), from the log deciding the day. A
    correction keeps the stored method, so: an absent day shows only TEACHER (a roll call
    or correction made the log), else null; a present day whose log had no method (absent
    in a live/face session, then changed by a teacher) shows TEACHER.
    """
    if log.status == PRESENT:
        return log.method or (AttendanceLog.Method.TEACHER if log.is_modified_by_teacher else None)
    return log.method if log.method == AttendanceLog.Method.TEACHER else None


def log_details(log) -> dict:
    """method / changed_by / changed_at of the log that decides a day (None without a log)."""
    if log is None:
        return {'method': None, 'changed_by': None, 'changed_at': None}
    return {
        'method': shown_method(log),
        'changed_by': person_name(log.changed_by) if log.changed_by_id and log.changed_by else None,
        'changed_at': iso_datetime(log.changed_at),
    }


def class_dates(course_info_ids) -> dict:
    """{course info id: [its class dates, newest first]}."""
    dates = defaultdict(set)
    rows = AttendanceLog.objects.filter(course_info_id__in=list(course_info_ids)).order_by()
    for ci_id, day in rows.values_list('course_info_id', 'date').distinct():
        dates[ci_id].add(day)
    return {ci_id: sorted(days, reverse=True) for ci_id, days in dates.items()}


def logs_by_day(course_info_ids, student) -> dict:
    """{course info id: {date: [the student's logs that day]}}."""
    result = defaultdict(lambda: defaultdict(list))
    logs = AttendanceLog.objects.filter(
        course_info_id__in=list(course_info_ids), student=student,
    ).select_related('changed_by')
    for log in logs:
        result[log.course_info_id][log.date].append(log)
    return result


def student_days(dates, membership, day_logs) -> list:
    """
    One row per class date (`dates`, newest first): {date, status, method, changed_by,
    changed_at}; status null outside the membership (and on the join day without a log);
    `day_logs` = {date: [logs]}.
    """
    rows = []
    for day in dates:
        logs = day_logs.get(day)
        if not membership.counts_on(day, bool(logs)):
            rows.append({'date': iso_date(day), 'status': None, **log_details(None)})
            continue
        status, deciding = day_status(logs) if logs else (ABSENT, None)
        rows.append({'date': iso_date(day), 'status': status, **log_details(deciding)})
    return rows


def count_days(days):
    """(attended, held) from student_days rows."""
    held = [d for d in days if d['status'] is not None]
    return sum(d['status'] == PRESENT for d in held), len(held)


def course_days(course_info, membership) -> list:
    """The student's day rows for one course (newest first)."""
    dates = class_dates([course_info.id]).get(course_info.id, [])
    return student_days(dates, membership, logs_by_day([course_info.id], membership.student)[course_info.id])


# ── Student dashboard (section 7) ─────────────────────────────────────────────

def get_student_attendance_summary(student_profile):
    """
    Attendance grouped by semester for the student dashboard: every semester whose class
    group they are or were in (history stays; left_at set = a former member), with that
    group's courses, sorted by level then term. Only dates inside their membership count.
    """
    memberships = (
        StudentClassroom.objects.filter(
            student=student_profile, classroom__deleted=False, classroom__semester__deleted=False,
        ).select_related('classroom__semester')
    )
    # One entry per semester (current membership first if there are several)
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
                # Older keys (same numbers)
                'totalClasses': held,
                'presentCount': attended,
                'percentage': round(attended / held * 100, 2) if held else 0.0,
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
            # Their membership: left_at set = a former member (history only, not current)
            'joined_at': iso_date(membership.joined_at),
            'left_at': iso_date(membership.left_at),
            'courses': courses_data,
        })

    return result


def student_course_detail(course_info, membership) -> dict:
    """GET /student/course-info/<id>/ for the signed-in student (section 7)."""
    days = course_days(course_info, membership)
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
        # Their membership: days before joined_at / from left_at on are outside it (status null)
        'joined_at': iso_date(membership.joined_at),
        'left_at': iso_date(membership.left_at),
        'attended': attended,
        'held': held,
        'percent': percent(attended, held),
        'classes_needed': classes_needed(attended, held),
        'days': [
            {'date': d['date'], 'status': d['status'], 'method': d['method'], 'changed': d['changed_at'] is not None}
            for d in days
        ],
    }
