from django.urls import path
from apps.users.views.admin import (
    PendingUsersView, VerifyUserView, RejectUserView, ResetUserPasswordView,
    AdminStudentListCreateView, AdminStudentDetailView, AdminStudentRestoreView, AdminStudentImportView,
    AdminTeacherListCreateView, AdminTeacherDetailView, AdminTeacherRestoreView,
)

urlpatterns = [
    path('users/pending/', PendingUsersView.as_view(), name='admin-users-pending'),
    path('users/<uuid:uuid>/verify/', VerifyUserView.as_view(), name='admin-users-verify'),
    path('users/<uuid:uuid>/reject/', RejectUserView.as_view(), name='admin-users-reject'),
    path('users/<uuid:uuid>/reset-password/', ResetUserPasswordView.as_view(), name='admin-users-reset-password'),
    path('students/', AdminStudentListCreateView.as_view(), name='admin-students'),
    path('students/import/', AdminStudentImportView.as_view(), name='admin-students-import'),
    path('students/<uuid:uuid>/', AdminStudentDetailView.as_view(), name='admin-student-detail'),
    path('students/<uuid:uuid>/restore/', AdminStudentRestoreView.as_view(), name='admin-student-restore'),
    path('teachers/', AdminTeacherListCreateView.as_view(), name='admin-teachers'),
    path('teachers/<uuid:uuid>/', AdminTeacherDetailView.as_view(), name='admin-teacher-detail'),
    path('teachers/<uuid:uuid>/restore/', AdminTeacherRestoreView.as_view(), name='admin-teacher-restore'),
]
