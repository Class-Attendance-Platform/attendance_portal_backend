"""Student: live sessions of their own courses and one course's days (API v2 sections 5 and 7)."""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo, StudentClassroom
from apps.attendance.models import AttendanceSession
from apps.attendance.records import student_course_detail
from apps.attendance.services import can_join_live, finalize_expired_sessions, live_sessions, session_ends_at
from apps.users.models import StudentProfile
from apps.users.permissions import IsStudent
from config.errors import error_response


def own_profile(request):
    return StudentProfile.objects.filter(user=request.user, user__deleted=False).first()


class StudentLiveSessionsView(APIView):
    """Live sessions of the student's current courses (the "Live now" banner). No token or code."""
    permission_classes = [IsStudent]

    def get(self, request):
        profile = own_profile(request)
        if profile is None:
            return Response({'success': True, 'sessions': []})
        classroom_ids = StudentClassroom.objects.filter(student=profile).current().values('classroom_id')
        sessions = AttendanceSession.objects.filter(
            is_active=True, mode=AttendanceSession.Mode.QR_ONLINE,
            course_info__classroom_id__in=classroom_ids, course_info__deleted=False,
            course_info__course__deleted=False, course_info__semester__deleted=False,
        ).select_related('course_info__course').order_by('started_at')

        rows = []
        for session, cache_data in live_sessions(sessions):
            if not can_join_live(profile, session):
                continue
            course = session.course_info.course
            submissions = (cache_data or {}).get('submissions', {})
            rows.append({
                'session_id': str(session.id),
                'course_info_id': str(session.course_info_id),
                'course': {'code': course.code, 'title': course.title},
                'delivery': session.delivery,
                'ends_at': session_ends_at(session, cache_data).isoformat(),
                'checked_in': str(profile.student_id) in submissions,
            })
        return Response({'success': True, 'sessions': rows})


class StudentCourseDetailView(APIView):
    """One of the signed-in student's courses: numbers and every class date."""
    permission_classes = [IsStudent]

    def get(self, request, uuid):
        ci = get_object_or_404(
            CourseInfo.objects.select_related('course', 'teacher__user', 'semester'),
            id=uuid, deleted=False, course__deleted=False, semester__deleted=False,
        )
        profile = own_profile(request)
        membership = (
            StudentClassroom.objects.filter(student=profile, classroom_id=ci.classroom_id).first()
            if profile else None
        )
        if membership is None:
            return error_response('This is not one of your courses.', status=403, code='not_enrolled')
        finalize_expired_sessions(ci)
        return Response({'success': True, **student_course_detail(ci, membership)})
