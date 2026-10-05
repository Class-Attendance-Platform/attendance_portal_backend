from django.urls import path

from apps.academic.views.teacher import (
    TeacherAttendanceCorrectionView,
    TeacherCourseInfoDetailView,
    TeacherCoursesView,
    TeacherDeleteDateView,
    TeacherLiveSessionView,
    TeacherRollCallView,
    TeacherStudentDetailView,
)

urlpatterns = [
    path("<uuid:uuid>/courses/", TeacherCoursesView.as_view(), name="teacher-courses"),
    path(
        "course-info/<uuid:uuid>/",
        TeacherCourseInfoDetailView.as_view(),
        name="teacher-course-info",
    ),
    path(
        "course-info/<uuid:uuid>/students/<uuid:profile_id>/",
        TeacherStudentDetailView.as_view(),
        name="teacher-course-student",
    ),
    path(
        "course-info/<uuid:uuid>/live/",
        TeacherLiveSessionView.as_view(),
        name="teacher-course-live",
    ),
    path(
        "course-info/<uuid:uuid>/attendance/",
        TeacherAttendanceCorrectionView.as_view(),
        name="teacher-attendance-correction",
    ),
    path(
        "course-info/<uuid:uuid>/roll-call/",
        TeacherRollCallView.as_view(),
        name="teacher-roll-call",
    ),
    path(
        "course-info/<uuid:uuid>/history-session/<str:date>/",
        TeacherDeleteDateView.as_view(),
        name="teacher-history-session-detail",
    ),
]
