from django.contrib.auth import get_user_model
from django.contrib.auth.models import update_last_login
from django.db import IntegrityError
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.serializers import (
    LoginSerializer, MeUpdateSerializer, NewAccountSerializer, PasswordChangeSerializer,
    PasswordForgotSerializer, PasswordResetSerializer, password_errors, user_data,
)
from apps.users.services import (
    blacklist_refresh_tokens, can_sign_in, email_reset_enabled, find_user_by_email, is_approved,
    send_password_reset_email, tokens_for, user_from_reset_link,
)
from apps.users.throttles import LoginIPThrottle, LoginThrottle, PasswordForgotThrottle, RegisterThrottle
from config.errors import error_response, validation_error_response

User = get_user_model()

FORGOT_SENT_MESSAGE = 'If an account exists for this email, we sent a link to reset the password.'


class PublicView(APIView):
    """No sign-in needed; a stale token in the request is ignored instead of answering 401."""
    permission_classes = [AllowAny]
    authentication_classes = []


class LoginView(PublicView):
    throttle_classes = [LoginThrottle, LoginIPThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        email = serializer.validated_data['email']
        password = serializer.validated_data['password']

        user = find_user_by_email(email, password)
        if user is None:
            User().set_password(password)  # same work as a real check, so timing tells nothing
        if user is None or not user.check_password(password):
            return error_response('Email or password is incorrect.', 401, code='invalid_credentials')
        if user.deleted or not user.is_active:
            return error_response(
                'This account has been disabled. Contact the department office.', 403, code='account_disabled'
            )
        if not is_approved(user):
            return error_response('Your account is waiting for admin approval.', 403, code='pending_approval')

        update_last_login(None, user)
        return Response({'success': True, **tokens_for(user), 'user': user_data(user)})


class RegisterView(PublicView):
    throttle_classes = [RegisterThrottle]

    def post(self, request):
        serializer = NewAccountSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        try:
            serializer.save()
        except IntegrityError:
            # Someone took the email or id between the check and the save: answer like the check.
            again = NewAccountSerializer(data=request.data)
            if not again.is_valid():
                return validation_error_response(again.errors)
            return error_response('This account could not be created. Please try again.', 400)
        return Response({
            'success': True,
            'status': 'pending',
            'message': 'Account created. An admin will approve it soon.',
        }, status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw = request.data.get('refresh')
        if raw:
            try:
                token = RefreshToken(str(raw))
                if str(token.payload.get('user_id')) == str(request.user.pk):
                    token.blacklist()
            except TokenError:
                pass  # already invalid: nothing left to sign out
        return Response({'success': True, 'message': 'Signed out.'})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'success': True, 'user': user_data(request.user)})

    def patch(self, request):
        serializer = MeUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        user = request.user
        changed = []
        for name, value in serializer.validated_data.items():
            setattr(user, name, value.strip())
            changed.append(name)
        if changed:
            user.save(update_fields=changed)
        return Response({'success': True, 'user': user_data(user)})


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        user = request.user
        current = serializer.validated_data['current_password']
        new = serializer.validated_data['new_password']
        if not user.check_password(current):
            return error_response('Your current password is incorrect.', 400, code='wrong_password')
        problems = password_errors(new, user)
        if not problems and new == current:
            problems = ['The new password must be different from the current one.']
        if problems:
            return validation_error_response({'new_password': problems})

        user.set_password(new)
        user.save(update_fields=['password'])
        blacklist_refresh_tokens(user)  # other devices are signed out
        return Response({'success': True, 'message': 'Password changed.', 'tokens': tokens_for(user)})


class PasswordForgotView(PublicView):
    throttle_classes = [PasswordForgotThrottle]

    def post(self, request):
        if not email_reset_enabled():
            return error_response(
                'Password reset by email is not set up. Ask an admin to reset your password.',
                503, code='email_not_configured',
            )
        serializer = PasswordForgotSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        user = find_user_by_email(serializer.validated_data['email'])
        # Same answer either way, so nobody can test which emails have accounts.
        if user is not None and can_sign_in(user) and user.has_usable_password():
            send_password_reset_email(user)
        return Response({'success': True, 'message': FORGOT_SENT_MESSAGE})


class PasswordResetView(PublicView):
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        data = serializer.validated_data
        user = user_from_reset_link(data['uid'], data['token'])
        if user is None:
            return error_response('This link is invalid or has expired.', 400, code='invalid_link')
        problems = password_errors(data['new_password'], user)
        if problems:
            return validation_error_response({'new_password': problems})

        user.set_password(data['new_password'])  # also makes the link unusable (one use)
        user.save(update_fields=['password'])
        blacklist_refresh_tokens(user)
        return Response({'success': True, 'message': 'Password changed. You can sign in with it now.'})
