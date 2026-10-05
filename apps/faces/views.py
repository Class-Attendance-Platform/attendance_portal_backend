import logging
import uuid as uuid_lib

from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academic.models import CourseInfo
from apps.attendance.views.sessions import semester_finished_response
from apps.faces import services
from apps.faces.engines import FaceEngineUnavailable
from apps.faces.models import StudentFace
from apps.faces.services import FaceError
from apps.users.models import StudentProfile
from apps.users.permissions import (
    IsAdmin, IsAdminOrTeacher, IsStudent, can_manage_course, not_your_course_response,
)
from config.errors import error_response

logger = logging.getLogger(__name__)
MAX_CLASS_PHOTOS = 3


def run_face_task(task):
    """Runs a face operation and turns known problems into clear API errors."""
    try:
        return task()
    except FaceError as e:
        return Response({'success': False, 'message': e.message}, status=e.status)
    except FaceEngineUnavailable:
        logger.exception('Face engine unavailable')
        return Response({
            'success': False,
            'message': 'Face recognition is not set up on the server yet. Please tell your admin.',
        }, status=503)


def _course(request):
    """
    The course named by course_info_id, or None if the id is missing or malformed, or the
    course-info, its course or its semester is deleted.
    """
    try:
        course_id = uuid_lib.UUID(str(request.data.get('course_info_id')))
    except ValueError:
        return None
    return CourseInfo.objects.select_related('semester').filter(
        id=course_id, deleted=False, course__deleted=False, semester__deleted=False,
    ).first()


def _course_error(request, ci):
    """An error response if the course can't take face attendance for this user, else None."""
    if ci is None:
        return error_response('Course not found.', status=404, code='not_found')
    if not can_manage_course(request.user, ci):
        return not_your_course_response()
    if not ci.semester.is_active:
        return semester_finished_response()
    return None


def _profile(request):
    return StudentProfile.objects.filter(user=request.user, user__deleted=False).first()


class MyFaceView(APIView):
    """A student's own face registration: status, register (3 photos), delete."""
    permission_classes = [IsStudent]

    def get(self, request):
        student = _profile(request)
        if not student:
            return Response({'success': False, 'message': 'Student not found.'}, status=404)
        return Response({'success': True, **services.face_status(student)})

    def post(self, request):
        student = _profile(request)
        if not student:
            return Response({'success': False, 'message': 'Student not found.'}, status=404)
        if str(request.data.get('consent', '')).lower() not in ('true', '1', 'yes'):
            return Response({'success': False, 'message': 'Please agree to the consent statement first.'}, status=400)
        uploads = {pose: request.FILES.get(pose.lower()) for pose in services.POSE_LABELS}
        missing = [services.POSE_LABELS[p] for p, f in uploads.items() if f is None]
        if missing:
            return Response({'success': False, 'message': f'Missing photo: {", ".join(missing)}.'}, status=400)

        def register():
            return Response({'success': True, **services.register_student_faces(student, uploads)}, status=201)
        return run_face_task(register)

    def delete(self, request):
        student = _profile(request)
        if not student:
            return Response({'success': False, 'message': 'Student not found.'}, status=404)
        StudentFace.objects.filter(student=student).delete()
        return Response({'success': True, 'message': 'Your face data was deleted.'})


class AdminFaceStatusView(APIView):
    """Which students have registered a face: {student profile id: registered_at}."""
    permission_classes = [IsAdmin]

    def get(self, request):
        registered = {}
        for student_id, created in StudentFace.objects.order_by('created_at').values_list('student_id', 'created_at'):
            registered.setdefault(str(student_id), created.isoformat())
        return Response({'success': True, 'registered': registered})


class AdminStudentFaceView(APIView):
    """Admin: see a student's registered face crops, or reset them."""
    permission_classes = [IsAdmin]

    def get(self, request, uuid):
        student = get_object_or_404(StudentProfile, id=uuid)
        faces = student.faces.order_by('pose')
        return Response({
            'success': True,
            **services.face_status(student),
            'crops': [{'pose': f.pose, 'image': services.jpeg_data_url(f.crop_jpeg)} for f in faces],
        })

    def delete(self, request, uuid):
        student = get_object_or_404(StudentProfile, id=uuid)
        deleted, _ = StudentFace.objects.filter(student=student).delete()
        return Response({'success': True, 'message': 'Face data reset.', 'deleted': deleted})


class RecognizeClassView(APIView):
    """Teacher sends 1-3 class photos; returns suggested attendance (nothing is saved)."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request):
        ci = _course(request)
        error = _course_error(request, ci)
        if error:
            return error
        photos = request.FILES.getlist('photos')
        if not 1 <= len(photos) <= MAX_CLASS_PHOTOS:
            return Response(
                {'success': False, 'message': f'Send 1 to {MAX_CLASS_PHOTOS} class photos.'}, status=400
            )
        return run_face_task(lambda: Response({'success': True, **services.recognize_class(ci, photos)}))


class ConfirmFaceAttendanceView(APIView):
    """Teacher confirms the reviewed list; saves it as a FACE session."""
    permission_classes = [IsAdminOrTeacher]

    def post(self, request):
        ci = _course(request)
        error = _course_error(request, ci)
        if error:
            return error
        present_ids = request.data.get('present_student_ids', [])
        if not isinstance(present_ids, list):
            return Response({'success': False, 'message': 'present_student_ids must be a list.'}, status=400)
        day = None
        if request.data.get('date'):
            try:
                day = parse_date(str(request.data['date']))
            except ValueError:
                day = None
            if day is None:
                return Response({'success': False, 'message': 'Use a YYYY-MM-DD date.'}, status=400)

        def save():
            session = services.save_face_attendance(ci, present_ids, day)
            return Response({
                'success': True,
                'session_id': str(session.id),
                'date': str(session.date),
                'total_present': session.logs.filter(status='PRESENT').count(),
            }, status=201)
        return run_face_task(save)
