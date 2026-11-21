from django.urls import path
from apps.attendance.views.sessions import (
    CancelSessionView,
    CheckInView,
    CourseAttendanceHistoryView,
    ExtendSessionView,
    LiveMarkView,
    SessionCodeView,
    SessionStatusView,
    StartSessionView,
    StopSessionView,
)

urlpatterns = [
    path('start/', StartSessionView.as_view(), name='session-start'),
    path('<uuid:uuid>/code/', SessionCodeView.as_view(), name='session-code'),
    path('<uuid:uuid>/status/', SessionStatusView.as_view(), name='session-status'),
    path('<uuid:uuid>/extend/', ExtendSessionView.as_view(), name='session-extend'),
    path('<uuid:uuid>/mark/', LiveMarkView.as_view(), name='session-mark'),
    path('<uuid:uuid>/stop/', StopSessionView.as_view(), name='session-stop'),
    path('<uuid:uuid>/cancel/', CancelSessionView.as_view(), name='session-cancel'),

    path('check-in/', CheckInView.as_view(), name='session-check-in'),

    path('course-info/<uuid:uuid>/history/', CourseAttendanceHistoryView.as_view(), name='course-history'),
]
