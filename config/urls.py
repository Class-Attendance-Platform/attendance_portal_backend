from django.urls import path, include

# No Django admin site: the app has its own admin pages, and an open /admin/ login would be a
# password-guessing route outside the API's sign-in limits.
urlpatterns = [
    path('api/auth/', include('apps.users.urls.auth')),
    path('api/config/', include('apps.users.urls.config')),
    path('api/admin/', include('apps.users.urls.admin')),
    path('api/admin/', include('apps.academic.urls.admin')),
    path('api/student/', include('apps.users.urls.student')),
    path('api/teacher/', include('apps.academic.urls.teacher')),
    path('api/sessions/', include('apps.attendance.urls')),
    path('api/reports/', include('apps.reports.urls')),
    path('api/faces/', include('apps.faces.urls')),
]

# Errors outside the API views answer JSON with a readable message too (config/errors.py)
handler404 = 'config.errors.json_not_found'
handler500 = 'config.errors.json_server_error'
