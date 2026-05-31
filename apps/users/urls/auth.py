from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from apps.users.views.auth import LoginView, RegisterView, MeView

urlpatterns = [
    path('login/', LoginView.as_view(), name='auth-login'),
    path('refresh/', TokenRefreshView.as_view(), name='auth-refresh'),
    path('register/', RegisterView.as_view(), name='auth-register'),
    path('me/', MeView.as_view(), name='auth-me'),
]
