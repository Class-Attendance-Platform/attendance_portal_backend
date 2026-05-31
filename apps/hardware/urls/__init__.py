from django.urls import path, include

urlpatterns = [
    path('', include('apps.hardware.urls.hardware')),
]
