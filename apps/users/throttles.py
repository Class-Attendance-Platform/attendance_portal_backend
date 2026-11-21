import hashlib
import logging

from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)

class FailOpenMixin:
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception('Request limit %s skipped: the cache is unreachable', getattr(self, 'scope', ''))
            return True

class IPRateThrottle(FailOpenMixin, SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}

class IPAndEmailRateThrottle(IPRateThrottle):
    def get_cache_key(self, request, view):
        data = getattr(request, 'data', None)
        email = str(data.get('email', '') if isinstance(data, dict) else '').strip().upper().lower()
        digest = hashlib.sha256(email.encode('utf-8', 'surrogatepass')).hexdigest()[:32]
        return self.cache_format % {'scope': self.scope, 'ident': f'{self.get_ident(request)}:{digest}'}

class LoginThrottle(IPAndEmailRateThrottle):
    scope = 'login'

class LoginIPThrottle(IPRateThrottle):
    scope = 'login_ip'

class RegisterThrottle(IPAndEmailRateThrottle):
    scope = 'register'

class RegisterIPThrottle(IPRateThrottle):
    scope = 'register_ip'

class PasswordForgotThrottle(IPRateThrottle):
    scope = 'password_forgot'

class PasswordChangeThrottle(FailOpenMixin, UserRateThrottle):
    scope = 'password_change'

class CheckInThrottle(FailOpenMixin, UserRateThrottle):
    scope = 'check_in'

class FaceRegisterThrottle(FailOpenMixin, UserRateThrottle):
    scope = 'face_register'
