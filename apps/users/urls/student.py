from django.urls import path
from apps.users.views.student import StudentAttendanceView, StudentDeviceBindingView

urlpatterns = [
    path('<uuid:uuid>/semesters/', StudentAttendanceView.as_view(), name='student-semesters'),
    path('verify-device/<int:student_id>/', StudentDeviceBindingView.as_view(), name='student-verify-device'),
]
