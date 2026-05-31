from django.urls import path
from apps.users.views.student import StudentAttendanceView, StudentDeviceBindingView

urlpatterns = [
    path('<uuid:uuid>/attendance/', StudentAttendanceView.as_view(), name='student-attendance'),
    path('verify-device/<int:student_id>/', StudentDeviceBindingView.as_view(), name='student-verify-device'),
]
