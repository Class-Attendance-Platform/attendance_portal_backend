from django.urls import path
from apps.academic.views.teacher import TeacherCoursesView, TeacherCourseInfoDetailView

urlpatterns = [
    path('<uuid:uuid>/courses/', TeacherCoursesView.as_view(), name='teacher-courses'),
    path('course-info/<uuid:uuid>/', TeacherCourseInfoDetailView.as_view(), name='teacher-course-info'),
]
