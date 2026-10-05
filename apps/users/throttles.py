"""
Request limits (API v2 conventions). Rates live in settings (REST_FRAMEWORK
DEFAULT_THROTTLE_RATES, overridable from .env); counts are kept in the cache (Redis).
"""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class IPRateThrottle(SimpleRateThrottle):
    """Counts per visitor IP, signed in or not."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class LoginThrottle(IPRateThrottle):
    scope = 'login'


class RegisterThrottle(IPRateThrottle):
    scope = 'register'


class PasswordForgotThrottle(IPRateThrottle):
    scope = 'password_forgot'


class CheckInThrottle(UserRateThrottle):
    """Per signed-in student (falls back to the IP)."""
    scope = 'check_in'
