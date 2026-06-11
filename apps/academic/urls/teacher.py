from django.urls import path

from apps.academic.views.teacher import (
    TeacherCourseInfoDetailView,
    TeacherCoursesView,
    TeacherHistorySessionView,
)

urlpatterns = [
    path("<uuid:uuid>/courses/", TeacherCoursesView.as_view(), name="teacher-courses"),
    path(
        "course-info/<uuid:uuid>/",
        TeacherCourseInfoDetailView.as_view(),
        name="teacher-course-info",
    ),
    # Manual History Endpoints
    path(
        "course-info/<uuid:uuid>/history-session/",
        TeacherHistorySessionView.as_view(),
        name="teacher-history-session",
    ),
    path(
        "course-info/<uuid:uuid>/history-session/<str:date>/",
        TeacherHistorySessionView.as_view(),
        name="teacher-history-session-detail",
    ),
]
