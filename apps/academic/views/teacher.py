"""
Teacher: courses, students, history and corrections (API v2 section 6). Admins pass
can_manage_course too (the admin UI is read-only here).
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo, StudentClassroom, sort_semesters
from apps.academic.serializers import iso_date, person_name, semester_brief
from apps.academic.stats import course_numbers
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.records import course_days, count_days, day_status, log_details, parse_day, percent
from apps.attendance.serializers import CorrectionSerializer, RollCallSerializer
from apps.attendance.services import (
    DateChanged, correct_attendance, finalize_ended_sessions, finalize_expired_sessions, live_session,
    live_sessions, roll_call, roll_call_list, roll_call_version, session_payload,
)
from apps.faces.models import StudentFace
from apps.users.models import TeacherProfile
from apps.users.permissions import IsAdminOrTeacher, can_manage_course, not_your_course_response
from config.errors import error_response, validation_error_response


def teacher_course(request, uuid):
    """(course info, None) if the user may manage it, else (None, error response); 404 when deleted."""
    ci = get_object_or_404(
        CourseInfo.objects.select_related('course', 'teacher__user', 'semester', 'classroom'),
        id=uuid, deleted=False, course__deleted=False, semester__deleted=False,
    )
    if not can_manage_course(request.user, ci):
        return None, not_your_course_response()
    return ci, None


def future_date_response():
    return error_response('You cannot take attendance for a future date.', status=400, code='future_date')


def course_rows(course_infos):
    """
    ({course info id: Course row}, numbers) for the teacher's lists: class list counts,
    classes held, average, below-minimum and face registration counts, live session.
    """
    course_infos = list(course_infos)
    numbers = course_numbers(course_infos)
    profile_ids = {pid for n in numbers.values() for pid in n.students}
    with_faces = set(
        StudentFace.objects.filter(student_id__in=profile_ids).values_list('student_id', flat=True).distinct()
    )
    live = {}
    for session, _ in live_sessions(AttendanceSession.objects.filter(course_info__in=course_infos)):
        live.setdefault(session.course_info_id, str(session.id))

    rows = {}
    for ci in course_infos:
        n = numbers[ci.id]
        rows[ci.id] = {
            'course_info_id': str(ci.id),
            'code': ci.course.code,
            'title': ci.course.title,
            'credits': ci.course.credits,
            'semester': semester_brief(ci.semester),
            'student_count': n.student_count,
            'classes_held': n.classes_held,
            'average_percent': n.average_percent,
            'below_min_count': n.below_min_count,
            'face_registered_count': sum(pid in with_faces for pid in n.students),
            'live_session_id': live.get(ci.id),
        }
    return rows, numbers, with_faces


class TeacherCoursesView(APIView):
    """{current: [Course], previous: [Course]} (previous = finished semesters)."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        teacher = get_object_or_404(TeacherProfile, Q(id=uuid) | Q(user_id=uuid), user__deleted=False)
        if request.user.role == "TEACHER" and teacher.user_id != request.user.id:
            return error_response('You can only view your own courses.', status=403, code='permission_denied')

        # Save any of this teacher's sessions whose timer ran out (e.g. tab was closed)
        finalize_ended_sessions(AttendanceSession.objects.filter(course_info__teacher=teacher))

        # Deleted courses and semesters are hidden from teachers
        course_infos = list(CourseInfo.objects.filter(
            teacher=teacher, deleted=False, course__deleted=False, semester__deleted=False,
        ).select_related("course", "semester"))
        order = {s.id: i for i, s in enumerate(sort_semesters({ci.semester for ci in course_infos}))}
        course_infos.sort(key=lambda ci: (order[ci.semester_id], ci.course.code))
        rows, _, _ = course_rows(course_infos)

        return Response({
            "success": True,
            "current": [rows[ci.id] for ci in course_infos if ci.semester.is_active],
            "previous": [rows[ci.id] for ci in course_infos if not ci.semester.is_active],
        })


class TeacherCourseInfoDetailView(APIView):
    """The course, its class list with each student's numbers, and its class dates."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error

        finalize_expired_sessions(ci)
        rows, numbers, with_faces = course_rows([ci])
        n = numbers[ci.id]

        students = []
        for profile_id, s in n.students.items():
            membership = s.membership
            students.append({
                'profile_id': str(profile_id),
                'student_id': membership.student.student_id,
                'name': person_name(membership.student.user),
                'joined_at': iso_date(membership.joined_at),
                'left_at': iso_date(membership.left_at),
                'attended': s.attended,
                'held': s.held,
                'percent': s.percent,
                'below_min': s.below_min,
                'face_registered': profile_id in with_faces,
            })

        # Each class date: how many were present / had a log (deleted accounts left out)
        present, total = defaultdict(set), defaultdict(set)
        logs = AttendanceLog.objects.filter(course_info=ci, student__user__deleted=False).order_by()
        for day, student_id, status in logs.values_list('date', 'student_id', 'status'):
            total[day].add(student_id)
            if status == AttendanceLog.Status.PRESENT:
                present[day].add(student_id)
        dates = [
            {'date': day.isoformat(), 'present': len(present[day]), 'total': len(total[day])}
            for day in n.class_dates
        ]

        return Response({'success': True, 'course': rows[ci.id], 'students': students, 'dates': dates})


def course_membership(ci, profile_id):
    """The student's membership in the course's class group (former members too), or None."""
    return (
        StudentClassroom.objects.filter(classroom_id=ci.classroom_id, student_id=profile_id)
        .active_accounts().select_related('student__user').first()
    )


class TeacherStudentDetailView(APIView):
    """One student in a course: numbers and every class date (status null = not enrolled)."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid, profile_id):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        membership = course_membership(ci, profile_id)
        if membership is None:
            return error_response('This student is not in this course.', status=404, code='not_found')

        finalize_expired_sessions(ci)
        days = course_days(ci, membership)
        attended, held = count_days(days)
        student = membership.student
        return Response({
            'success': True,
            'student': {
                'profile_id': str(student.id),
                'student_id': student.student_id,
                'name': person_name(student.user),
                'email': student.user.email,
                'joined_at': iso_date(membership.joined_at),
                'left_at': iso_date(membership.left_at),
                'attended': attended,
                'held': held,
                'percent': percent(attended, held),
            },
            'days': days,
        })


class TeacherLiveSessionView(APIView):
    """The course's live session (reopen after a reload), or null."""
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        session, cache_data = live_session(ci)
        return Response({'success': True, 'session': session_payload(session, cache_data) if session else None})


class TeacherAttendanceCorrectionView(APIView):
    """PUT {date, profile_id, status}: changes only that student on a date that has a class."""
    permission_classes = [IsAdminOrTeacher]

    def put(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        serializer = CorrectionSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        day = data['date']
        if day > timezone.localdate():
            return future_date_response()

        finalize_expired_sessions(ci)
        if not AttendanceLog.objects.filter(course_info=ci, date=day).exists():
            return error_response(
                'There was no class on this date. Use roll call to add it.', status=404, code='no_class_on_date',
            )
        membership = course_membership(ci, data['profile_id'])
        if membership is None or not membership.covers(day):
            return error_response(
                'This student was not in the class on that date.', status=400, code='not_enrolled',
            )

        changed = correct_attendance(ci, membership.student, day, data['status'], request.user)
        logs = list(AttendanceLog.objects.filter(
            course_info=ci, student=membership.student, date=day,
        ).select_related('changed_by'))
        status, deciding = day_status(logs)
        return Response({
            'success': True,
            'message': 'Attendance changed.' if changed else 'Nothing to change.',
            'changed': changed,
            'day': {'date': day.isoformat(), 'status': status, **log_details(deciding)},
        })


def live_session_on(ci, day):
    """The course's live session on that date (still taking check-ins), or None."""
    found = live_sessions(AttendanceSession.objects.filter(course_info=ci, date=day))
    return found[0][0] if found else None


def session_running_response(session):
    return error_response(
        'A live session is running on this date. Mark students present in the session, or stop it first.',
        status=409, code='session_running', session_id=str(session.id),
    )


class TeacherRollCallView(APIView):
    """
    GET ?date=: whom a roll call for that date covers (everyone enrolled that day, former members
    too) with their status. POST {date, present_profile_ids, version?}: saves a roll call
    (creates the class if missing).
    """
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        day = parse_day(request.query_params.get('date', ''))
        if day is None:
            return validation_error_response({'date': ['Use a YYYY-MM-DD date.']})
        if day > timezone.localdate():
            return future_date_response()

        finalize_expired_sessions(ci)
        session = live_session_on(ci, day)
        rows = roll_call_list(ci, day)
        return Response({
            'success': True,
            'date': day.isoformat(),
            'has_class': AttendanceLog.objects.filter(course_info=ci, date=day).exists(),
            'live_session_id': str(session.id) if session else None,
            'version': roll_call_version(rows),
            'students': [
                {
                    'profile_id': str(m.student_id),
                    'student_id': m.student.student_id,
                    'name': person_name(m.student.user),
                    'joined_at': iso_date(m.joined_at),
                    'left_at': iso_date(m.left_at),
                    'status': status,
                }
                for m, status in rows
            ],
        })

    def post(self, request, uuid):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        serializer = RollCallSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        day = data['date']
        if day > timezone.localdate():
            return future_date_response()

        finalize_expired_sessions(ci)
        session = live_session_on(ci, day)
        if session is not None:
            return session_running_response(session)
        try:
            counts = roll_call(ci, day, data['present_profile_ids'], request.user, data.get('version'))
        except DateChanged:
            return error_response(
                'This date changed since you opened it. Check the list and save again.',
                status=409, code='date_changed',
            )
        return Response({
            'success': True,
            'message': f'Roll call saved for {day:%d %b %Y}.',
            'date': day.isoformat(),
            **counts,
        })


class TeacherDeleteDateView(APIView):
    """DELETE .../history-session/<date>/: deletes that date's classes and logs."""
    permission_classes = [IsAdminOrTeacher]

    def delete(self, request, uuid, date):
        ci, error = teacher_course(request, uuid)
        if error:
            return error
        day = parse_day(date)
        if day is None:
            return validation_error_response({'date': ['Use a YYYY-MM-DD date.']})

        finalize_expired_sessions(ci)
        if AttendanceSession.objects.filter(course_info=ci, date=day, is_active=True).exists():
            return error_response(
                'A live session is running on this date. Stop or cancel it first.',
                status=409, code='session_running',
            )
        with transaction.atomic():
            logs = AttendanceLog.objects.filter(course_info=ci, date=day)
            deleted_count = logs.count()
            AttendanceSession.objects.filter(course_info=ci, date=day, is_active=False).delete()
            logs.delete()

        return Response({
            'success': True,
            'message': f'The class on {day:%d %b %Y} was deleted.',
            'deleted': deleted_count,
        })
