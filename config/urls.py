from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.users.urls.auth')),
    path('api/config/', include('apps.users.urls.config')),
    path('api/admin/', include('apps.users.urls.admin')),
    path('api/admin/', include('apps.academic.urls.admin')),
    path('api/student/', include('apps.users.urls.student')),
    path('api/teacher/', include('apps.academic.urls.teacher')),
    path('api/sessions/', include('apps.attendance.urls')),
    path('api/hardware/', include('apps.hardware.urls')),
    path('api/reports/', include('apps.reports.urls')),
]
