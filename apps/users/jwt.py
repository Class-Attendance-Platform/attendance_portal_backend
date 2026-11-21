from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.utils import get_md5_hash_password

PASSWORD_CHANGED_MESSAGE = 'Your password was changed. Please sign in again.'

def _user_for(payload):
    user_id = payload.get(api_settings.USER_ID_CLAIM)
    return get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first() if user_id else None

def _password_changed_since(raw) -> bool:
    if not api_settings.CHECK_REVOKE_TOKEN:
        return False
    try:
        payload = token_backend.decode(str(raw), verify=True)
    except TokenBackendError:
        return False
    held = payload.get(api_settings.REVOKE_TOKEN_CLAIM)
    user = _user_for(payload)
    return held is not None and user is not None and held != get_md5_hash_password(user.password)

class PasswordCheckedRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            refresh = self.token_class(attrs['refresh'])
        except TokenError:
            if _password_changed_since(attrs['refresh']):
                raise AuthenticationFailed(PASSWORD_CHANGED_MESSAGE, code='password_changed')
            raise

        user = _user_for(refresh.payload)
        if user is None:
            raise AuthenticationFailed('This account no longer exists.', code='user_not_found')
        if not api_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(self.error_messages['no_active_account'], code='no_active_account')

        if api_settings.CHECK_REVOKE_TOKEN:
            claim = api_settings.REVOKE_TOKEN_CLAIM
            current = get_md5_hash_password(user.password)
            held = refresh.payload.get(claim)
            if held is None:
                refresh[claim] = current
            elif held != current:
                raise AuthenticationFailed(PASSWORD_CHANGED_MESSAGE, code='password_changed')

        data = {'access': str(refresh.access_token)}
        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()
            data['refresh'] = str(refresh)
        return data
