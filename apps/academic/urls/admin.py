from django.urls import path
from apps.academic.views.admin import (
    SemesterListCreateView, SemesterDetailView,
    CourseListCreateView, CourseDetailView,
    ClassroomListCreateView, ClassroomDetailView,
    ClassroomStudentsView, ClassroomPromoteView,
    CourseInfoListCreateView, CourseInfoDetailView,
)

urlpatterns = [
    # Semesters
    path('semesters/', SemesterListCreateView.as_view(), name='admin-semesters'),
    path('semesters/<uuid:uuid>/', SemesterDetailView.as_view(), name='admin-semester-detail'),

    # Courses
    path('courses/', CourseListCreateView.as_view(), name='admin-courses'),
    path('courses/<uuid:uuid>/', CourseDetailView.as_view(), name='admin-course-detail'),

    # Classrooms
    path('classrooms/', ClassroomListCreateView.as_view(), name='admin-classrooms'),
    path('classrooms/<uuid:uuid>/', ClassroomDetailView.as_view(), name='admin-classroom-detail'),
    path('classrooms/<uuid:uuid>/students/', ClassroomStudentsView.as_view(), name='admin-classroom-students'),
    path('classrooms/<uuid:uuid>/promote/', ClassroomPromoteView.as_view(), name='admin-classroom-promote'),

    # CourseInfo
    path('course-info/', CourseInfoListCreateView.as_view(), name='admin-course-info'),
    path('course-info/<uuid:uuid>/', CourseInfoDetailView.as_view(), name='admin-course-info-detail'),
]
