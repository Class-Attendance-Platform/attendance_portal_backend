from collections import defaultdict

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo
from apps.academic.serializers import StudentInClassroomSerializer
from apps.attendance.models import AttendanceLog, AttendanceSession
from apps.attendance.services import finalize_ended_sessions, finalize_expired_sessions
from apps.users.models import TeacherProfile
from apps.users.permissions import IsAdminOrTeacher, can_manage_course, not_your_course_response


class TeacherCoursesView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        teacher = get_object_or_404(TeacherProfile, Q(id=uuid) | Q(user_id=uuid), user__deleted=False)
        if request.user.role == "TEACHER" and teacher.user_id != request.user.id:
            return Response(
                {"success": False, "message": "You can only view your own courses."}, status=403
            )

        all_cis = CourseInfo.objects.filter(
            teacher=teacher, deleted=False
        ).select_related("course", "semester", "classroom")

        # Save any of this teacher's sessions whose timer ran out (e.g. tab was closed)
        finalize_ended_sessions(AttendanceSession.objects.filter(course_info__teacher=teacher))

        # Split active vs previous semesters
        current = [ci for ci in all_cis if ci.semester.is_active]
        previous = [ci for ci in all_cis if not ci.semester.is_active]

        def serialize(ci):
            return {
                "id": str(ci.id),
                "course": {
                    "id": str(ci.course.id),
                    "code": ci.course.code,
                    "title": ci.course.title,
                    "credits": ci.course.credits,
                },
                "semester": {
                    "id": str(ci.semester.id),
                    "level": ci.semester.level,
                    "semester": ci.semester.semester,
                },
                "classroom": {
                    "id": str(ci.classroom.id),
                    "name": ci.classroom.name,
                },
            }

        return Response(
            {
                "success": True,
                "currentCourses": [serialize(ci) for ci in current],
                "previousCourses": [serialize(ci) for ci in previous],
            }
        )


class TeacherCourseInfoDetailView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        ci = get_object_or_404(
            CourseInfo.objects.select_related(
                "course", "teacher__user", "semester", "classroom"
            ),
            id=uuid,
            deleted=False,
        )
        if not can_manage_course(request.user, ci):
            return not_your_course_response()

        finalize_expired_sessions(ci)

        # Get enrolled students via classroom
        memberships = ci.classroom.memberships.select_related("student__user")
        students = [m.student for m in memberships]

        # Calculate attendance stats (a fixed number of queries, whatever the class size).
        # One class = one date: a student present in any session that day counts once.
        logs = AttendanceLog.objects.filter(course_info=ci)
        present_logs = logs.filter(status='PRESENT')
        dates = list(logs.values_list('date', flat=True).distinct().order_by('-date'))
        total_classes = len(dates)

        present_counts = dict(
            present_logs.order_by().values_list('student__student_id').annotate(n=Count('date', distinct=True))
        )
        attendance_map = {s.student_id: present_counts.get(s.student_id, 0) for s in students}

        present_by_date = defaultdict(list)
        present_pairs = present_logs.order_by('date', 'student__student_id').values_list(
            'date', 'student__student_id'
        ).distinct()
        for d, sid in present_pairs:
            present_by_date[d].append(sid)
        history = [
            {'date': str(d), 'presentStudents': present_by_date.get(d, [])}
            for d in dates
        ]

        return Response(
            {
                "success": True,
                "courseInfo": {
                    "id": str(ci.id),
                    "course": {
                        "id": str(ci.course.id),
                        "code": ci.course.code,
                        "title": ci.course.title,
                        "credits": ci.course.credits,
                    },
                    "teacher": {
                        "id": str(ci.teacher.id) if ci.teacher else None,
                        "userName": ci.teacher.user.get_full_name()
                        if ci.teacher
                        else None,
                        "email": ci.teacher.user.email if ci.teacher else None,
                    },
                    "semester": {
                        "id": str(ci.semester.id),
                        "level": ci.semester.level,
                        "semester": ci.semester.semester,
                        "is_active": ci.semester.is_active,
                    },
                    "classroom": {
                        "id": str(ci.classroom.id),
                        "name": ci.classroom.name,
                    },
                    "students": StudentInClassroomSerializer(students, many=True).data,
                    "attendance": {
                        "id": str(ci.id),
                        "totalClasses": total_classes,
                        "attendanceMap": attendance_map,
                        "history": history
                    }
                },
            }
        )


class TeacherHistorySessionView(APIView):
    """
    Handles bulk manual attendance saving and deletion for a specific date.
    Used by the "Add Attendance" feature in Teacher Dashboard.
    """

    permission_classes = [IsAdminOrTeacher]

    def post(self, request, uuid):
        """Save/Update bulk manual attendance for a date."""
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        if not can_manage_course(request.user, ci):
            return not_your_course_response()

        date_str = request.data.get("date")
        if not date_str:
            return Response(
                {"success": False, "message": "Date is required."}, status=400
            )
        try:
            day = parse_date(str(date_str))  # also accepts e.g. 2026-10-4
            # Numeric student IDs from the frontend (numbers or numeric strings)
            present_student_ids = {int(x) for x in request.data.get("presentStudentIds", [])}
        except (TypeError, ValueError):
            day = None
        if day is None:
            return Response(
                {"success": False, "message": "Use a YYYY-MM-DD date and numeric student IDs."},
                status=400,
            )

        # 1. Get all student profiles in this classroom
        memberships = ci.classroom.memberships.select_related("student")
        all_students = [m.student for m in memberships]
        all_student_map = {s.student_id: s for s in all_students}

        # 2. Clear existing manual logs for this date/course to avoid duplicates
        AttendanceLog.objects.filter(course_info=ci, date=day).delete()

        # 3. Create new logs
        logs_to_create = []
        for s_id, profile in all_student_map.items():
            is_present = s_id in present_student_ids
            logs_to_create.append(
                AttendanceLog(
                    course_info=ci,
                    student=profile,
                    date=day,
                    status="PRESENT" if is_present else "ABSENT",
                    source="MANUAL",
                    is_modified_by_teacher=True,
                )
            )

        AttendanceLog.objects.bulk_create(logs_to_create)

        return Response(
            {
                "success": True,
                "message": f"Attendance for {day} saved successfully.",
            }
        )

    def delete(self, request, uuid, date):
        """Delete all attendance logs for a specific date and course."""
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        if not can_manage_course(request.user, ci):
            return not_your_course_response()
        deleted_count, _ = AttendanceLog.objects.filter(
            course_info=ci, date=date
        ).delete()

        return Response(
            {
                "success": True,
                "message": f"Attendance for {date} deleted. {deleted_count} logs removed.",
            }
        )
