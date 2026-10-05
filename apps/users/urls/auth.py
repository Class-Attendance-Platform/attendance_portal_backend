from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from apps.users.views.auth import (
    LoginView, LogoutView, MeView, PasswordChangeView, PasswordForgotView, PasswordResetView, RegisterView,
)

urlpatterns = [
    path('login/', LoginView.as_view(), name='auth-login'),
    path('refresh/', TokenRefreshView.as_view(), name='auth-refresh'),
    path('register/', RegisterView.as_view(), name='auth-register'),
    path('logout/', LogoutView.as_view(), name='auth-logout'),
    path('me/', MeView.as_view(), name='auth-me'),
    path('password/change/', PasswordChangeView.as_view(), name='auth-password-change'),
    path('password/forgot/', PasswordForgotView.as_view(), name='auth-password-forgot'),
    path('password/reset/', PasswordResetView.as_view(), name='auth-password-reset'),
]
