from django.urls import path
from apps.attendance.views.sessions import (
    StartSessionView,
    StopSessionView,
    SessionStatusView,
    QROnlineCheckinView,
    ManualMarkView,
    CourseAttendanceHistoryView,
)

urlpatterns = [
    # Session lifecycle
    path('start/', StartSessionView.as_view(), name='session-start'),
    path('<uuid:uuid>/stop/', StopSessionView.as_view(), name='session-stop'),
    path('<uuid:uuid>/status/', SessionStatusView.as_view(), name='session-status'),

    # Student QR online check-in
    path('<uuid:uuid>/checkin/', QROnlineCheckinView.as_view(), name='session-checkin'),

    # Teacher manual mark
    path('<uuid:uuid>/mark/', ManualMarkView.as_view(), name='session-mark'),

    # Course attendance history (teacher/admin)
    path('course-info/<uuid:uuid>/history/', CourseAttendanceHistoryView.as_view(), name='course-history'),
]
