from django.urls import path
from apps.users.views.admin import (
    PendingUsersView, VerifyUserView,
    AdminStudentListCreateView, AdminStudentDetailView,
    AdminTeacherListCreateView, AdminTeacherDetailView,
    AdminUnbindDeviceView,
)

urlpatterns = [
    path('users/pending/', PendingUsersView.as_view(), name='admin-users-pending'),
    path('users/<uuid:uuid>/verify/', VerifyUserView.as_view(), name='admin-users-verify'),
    path('students/', AdminStudentListCreateView.as_view(), name='admin-students'),
    path('students/<uuid:uuid>/', AdminStudentDetailView.as_view(), name='admin-student-detail'),
    path('teachers/', AdminTeacherListCreateView.as_view(), name='admin-teachers'),
    path('teachers/<uuid:uuid>/', AdminTeacherDetailView.as_view(), name='admin-teacher-detail'),
    path('devices/<uuid:uuid>/unbind/', AdminUnbindDeviceView.as_view(), name='admin-unbind-device'),
]
