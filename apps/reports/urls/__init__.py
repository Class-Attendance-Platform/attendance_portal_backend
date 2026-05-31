from django.urls import path, include

urlpatterns = [
    path('', include('apps.reports.urls.reports')),
]
