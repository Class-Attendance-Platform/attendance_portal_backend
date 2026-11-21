from django.urls import path

from apps.faces.views import (
    AdminFaceStatusView, AdminStudentFaceView, ConfirmFaceAttendanceView, MyFaceView, RecognizeClassView,
)

urlpatterns = [
    path('me/', MyFaceView.as_view(), name='faces-me'),

    path('recognize/', RecognizeClassView.as_view(), name='faces-recognize'),
    path('confirm/', ConfirmFaceAttendanceView.as_view(), name='faces-confirm'),

    path('admin/students/', AdminFaceStatusView.as_view(), name='faces-admin-status'),
    path('admin/students/<uuid:uuid>/', AdminStudentFaceView.as_view(), name='faces-admin-student'),
]
