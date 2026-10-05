from django.urls import path
from apps.academic.views.admin import (
    AdminOverviewView,
    SemesterListCreateView, SemesterDetailView,
    SemesterFinishView, SemesterReopenView, SemesterRestoreView,
    SemesterStudentsView, SemesterStudentsRemoveView, SemesterCoursesView, SemesterPromoteView,
    CourseListCreateView, CourseDetailView, CourseRestoreView,
    ClassroomListCreateView, ClassroomDetailView,
    ClassroomStudentsView, ClassroomPromoteView,
    CourseInfoListCreateView, CourseInfoDetailView,
)

urlpatterns = [
    path('overview/', AdminOverviewView.as_view(), name='admin-overview'),

    # Semesters
    path('semesters/', SemesterListCreateView.as_view(), name='admin-semesters'),
    path('semesters/<uuid:uuid>/', SemesterDetailView.as_view(), name='admin-semester-detail'),
    path('semesters/<uuid:uuid>/finish/', SemesterFinishView.as_view(), name='admin-semester-finish'),
    path('semesters/<uuid:uuid>/reopen/', SemesterReopenView.as_view(), name='admin-semester-reopen'),
    path('semesters/<uuid:uuid>/restore/', SemesterRestoreView.as_view(), name='admin-semester-restore'),
    path('semesters/<uuid:uuid>/students/', SemesterStudentsView.as_view(), name='admin-semester-students'),
    path('semesters/<uuid:uuid>/students/remove/', SemesterStudentsRemoveView.as_view(),
         name='admin-semester-students-remove'),
    path('semesters/<uuid:uuid>/courses/', SemesterCoursesView.as_view(), name='admin-semester-courses'),
    path('semesters/<uuid:uuid>/promote/', SemesterPromoteView.as_view(), name='admin-semester-promote'),

    # Courses
    path('courses/', CourseListCreateView.as_view(), name='admin-courses'),
    path('courses/<uuid:uuid>/', CourseDetailView.as_view(), name='admin-course-detail'),
    path('courses/<uuid:uuid>/restore/', CourseRestoreView.as_view(), name='admin-course-restore'),

    # Classrooms (older endpoints; the redesigned app never shows classrooms)
    path('classrooms/', ClassroomListCreateView.as_view(), name='admin-classrooms'),
    path('classrooms/<uuid:uuid>/', ClassroomDetailView.as_view(), name='admin-classroom-detail'),
    path('classrooms/<uuid:uuid>/students/', ClassroomStudentsView.as_view(), name='admin-classroom-students'),
    path('classrooms/<uuid:uuid>/promote/', ClassroomPromoteView.as_view(), name='admin-classroom-promote'),

    # CourseInfo
    path('course-info/', CourseInfoListCreateView.as_view(), name='admin-course-info'),
    path('course-info/<uuid:uuid>/', CourseInfoDetailView.as_view(), name='admin-course-info-detail'),
]
