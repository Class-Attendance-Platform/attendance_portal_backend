from django.urls import path
from apps.attendance.views.student import StudentCourseDetailView, StudentLiveSessionsView
from apps.users.views.student import StudentAttendanceView, StudentDeviceBindingView

urlpatterns = [
    path('<uuid:uuid>/semesters/', StudentAttendanceView.as_view(), name='student-semesters'),
    path('live/', StudentLiveSessionsView.as_view(), name='student-live-sessions'),
    path('course-info/<uuid:uuid>/', StudentCourseDetailView.as_view(), name='student-course-info'),
    path('verify-device/<int:student_id>/', StudentDeviceBindingView.as_view(), name='student-verify-device'),
]
