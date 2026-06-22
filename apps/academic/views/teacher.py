from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo, Semester
from apps.academic.serializers import CourseInfoSerializer, StudentInClassroomSerializer
from apps.attendance.models import AttendanceLog
from apps.users.models import StudentProfile, TeacherProfile
from apps.users.permissions import IsAdminOrTeacher, IsTeacher


class TeacherCoursesView(APIView):
    permission_classes = [IsAdminOrTeacher]

    def get(self, request, uuid):
        from django.db.models import Q
        teacher = get_object_or_404(TeacherProfile, Q(id=uuid) | Q(user_id=uuid), user__deleted=False)

        all_cis = CourseInfo.objects.filter(
            teacher=teacher, deleted=False
        ).select_related("course", "semester", "classroom")

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

        # Get enrolled students via classroom
        memberships = ci.classroom.memberships.select_related("student__user")
        students = [m.student for m in memberships]

        # Calculate attendance stats
        logs = AttendanceLog.objects.filter(course_info=ci)
        dates = logs.values_list('date', flat=True).distinct().order_by('-date')
        total_classes = dates.count()

        attendance_map = {}
        for s in students:
            present_count = logs.filter(student=s, status='PRESENT').count()
            attendance_map[s.student_id] = present_count

        history = []
        for d in dates:
            present_student_ids = list(
                logs.filter(date=d, status='PRESENT').values_list('student__student_id', flat=True)
            )
            history.append({
                'date': str(d),
                'presentStudents': present_student_ids
            })

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
        date_str = request.data.get("date")
        present_student_ids = request.data.get(
            "presentStudentIds", []
        )  # These are numeric IDs from frontend

        if not date_str:
            return Response(
                {"success": False, "message": "Date is required."}, status=400
            )

        # 1. Get all student profiles in this classroom
        memberships = ci.classroom.memberships.select_related("student")
        all_students = [m.student for m in memberships]
        all_student_map = {s.student_id: s for s in all_students}

        # 2. Clear existing manual logs for this date/course to avoid duplicates
        AttendanceLog.objects.filter(course_info=ci, date=date_str).delete()

        # 3. Create new logs
        logs_to_create = []
        for s_id, profile in all_student_map.items():
            is_present = s_id in present_student_ids
            logs_to_create.append(
                AttendanceLog(
                    course_info=ci,
                    student=profile,
                    date=date_str,
                    status="PRESENT" if is_present else "ABSENT",
                    source="MANUAL",
                    is_modified_by_teacher=True,
                )
            )

        AttendanceLog.objects.bulk_create(logs_to_create)

        return Response(
            {
                "success": True,
                "message": f"Attendance for {date_str} saved successfully.",
            }
        )

    def delete(self, request, uuid, date):
        """Delete all attendance logs for a specific date and course."""
        ci = get_object_or_404(CourseInfo, id=uuid, deleted=False)
        deleted_count, _ = AttendanceLog.objects.filter(
            course_info=ci, date=date
        ).delete()

        return Response(
            {
                "success": True,
                "message": f"Attendance for {date} deleted. {deleted_count} logs removed.",
            }
        )
