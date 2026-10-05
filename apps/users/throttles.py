"""
Request limits (API v2 conventions). Rates live in settings (REST_FRAMEWORK
DEFAULT_THROTTLE_RATES, overridable from .env); counts are kept in the cache (Redis).
When the cache cannot be reached the limits are skipped (logged), so signing in never
depends on Redis.
"""
import logging

from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)


class FailOpenMixin:
    """Lets the request through when the counts cannot be read or written (cache down)."""

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception('Request limit %s skipped: the cache is unreachable', getattr(self, 'scope', ''))
            return True


class IPRateThrottle(FailOpenMixin, SimpleRateThrottle):
    """Counts per visitor IP, signed in or not."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class IPAndEmailRateThrottle(IPRateThrottle):
    """
    Counts per visitor IP *and* the email in the request body. A whole class signs in from one
    campus IP at the start of a lesson, so a per-IP count alone would lock them out; per email it
    still stops password guessing on one account.
    """

    def get_cache_key(self, request, view):
        email = str(request.data.get('email', '') if hasattr(request, 'data') else '').strip().lower()
        return self.cache_format % {'scope': self.scope, 'ident': f'{self.get_ident(request)}:{email}'}


class LoginThrottle(IPAndEmailRateThrottle):
    scope = 'login'


class LoginIPThrottle(IPRateThrottle):
    """A wide cap per IP on top, against trying many accounts from one address."""
    scope = 'login_ip'


class RegisterThrottle(IPRateThrottle):
    scope = 'register'


class PasswordForgotThrottle(IPRateThrottle):
    scope = 'password_forgot'


class CheckInThrottle(FailOpenMixin, UserRateThrottle):
    """Per signed-in student (falls back to the IP)."""
    scope = 'check_in'
