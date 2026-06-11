"""
Business logic layer for attendance — keeps views thin.
"""
from django.utils import timezone

from .models import AttendanceSession, AttendanceLog
from apps.academic.models import StudentClassroom


def commit_session_to_db(session: AttendanceSession, submissions: dict):
    """
    Called when a session is stopped.
    Creates PRESENT logs for submitted students,
    ABSENT logs for everyone else in the classroom.
    submissions: { "<student_int_id>": {"name": str, "mac": str} }
    """
    enrolled = StudentClassroom.objects.filter(
        classroom=session.course_info.classroom
    ).select_related('student')

    submitted_ids = {int(k) for k in submissions.keys()}
    now_time = timezone.now().time()

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
            source=session.mode,
        ))

    AttendanceLog.objects.bulk_create(logs_to_create, ignore_conflicts=True)


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
            ).order_by('date')

            total = logs.count()
            present = logs.filter(status=AttendanceLog.Status.PRESENT).count()
            percentage = round((present / total * 100), 2) if total > 0 else 0.0

            history = [
                {'date': str(log.date), 'present': log.status == 'PRESENT'}
                for log in logs
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
