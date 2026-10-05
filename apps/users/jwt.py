"""
Sign-in tokens that stop working when the password changes.

SIMPLE_JWT['CHECK_REVOKE_TOKEN'] puts a fingerprint of the password hash into every token
(claim `hash_password`) and refuses an access token whose fingerprint no longer matches
(401 `password_changed`). So a password change, a reset by email or an admin reset ends the
other devices' access at once, not only their refresh tokens (which are blacklisted too).

Refresh tokens issued before this setting have no fingerprint. Refreshing one adds the
current fingerprint: a refresh token that is not blacklisted is still valid, because every
password change blacklists them all. So nobody is signed out by the switch: an older access
token gets one 401, the app refreshes and goes on.
"""
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.utils import get_md5_hash_password

PASSWORD_CHANGED_MESSAGE = 'Your password was changed. Please sign in again.'


class PasswordCheckedRefreshSerializer(TokenRefreshSerializer):
    """POST /auth/refresh/: like SimpleJWT's, plus the password fingerprint check."""

    def validate(self, attrs):
        refresh = self.token_class(attrs['refresh'])  # signature, expiry, blacklist

        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        user = get_user_model().objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first() if user_id else None
        if user is None:
            raise AuthenticationFailed('This account no longer exists.', code='user_not_found')
        if not api_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(self.error_messages['no_active_account'], code='no_active_account')

        if api_settings.CHECK_REVOKE_TOKEN:
            claim = api_settings.REVOKE_TOKEN_CLAIM
            current = get_md5_hash_password(user.password)
            held = refresh.payload.get(claim)
            if held is None:
                refresh[claim] = current  # an older token: give it the fingerprint (see above)
            elif held != current:
                raise AuthenticationFailed(PASSWORD_CHANGED_MESSAGE, code='password_changed')

        data = {'access': str(refresh.access_token)}
        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:  # blacklist app not installed
                    pass
            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()
            data['refresh'] = str(refresh)
        return data
