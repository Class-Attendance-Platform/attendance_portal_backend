from django.urls import path

from apps.faces.views import (
    AdminFaceStatusView, AdminStudentFaceView, ConfirmFaceAttendanceView, MyFaceView, RecognizeClassView,
)

urlpatterns = [
    # Student: own face registration
    path('me/', MyFaceView.as_view(), name='faces-me'),

    # Teacher: class photos -> suggestions -> confirm
    path('recognize/', RecognizeClassView.as_view(), name='faces-recognize'),
    path('confirm/', ConfirmFaceAttendanceView.as_view(), name='faces-confirm'),

    # Admin: who registered, view or reset a student's face
    path('admin/students/', AdminFaceStatusView.as_view(), name='faces-admin-status'),
    path('admin/students/<uuid:uuid>/', AdminStudentFaceView.as_view(), name='faces-admin-student'),
]
