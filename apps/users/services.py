"""Account helpers shared by the auth and admin views and the management commands."""
import logging
import secrets
import string

from django.conf import settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher, get_hasher, make_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.constants import APP_NAME
from apps.users.models import User

logger = logging.getLogger(__name__)


def iso(value):
    """Date-time as ISO 8601 in local time (Asia/Dhaka, with offset), or None."""
    return timezone.localtime(value).isoformat() if value else None


def is_approved(user) -> bool:
    """Signed-up accounts wait for an admin; admins are never created by sign-up."""
    return user.is_verified or user.role == User.Role.ADMIN or user.is_superuser


def can_sign_in(user) -> bool:
    return user.is_active and not user.deleted and is_approved(user)


def find_user_by_email(email, password=None):
    """
    The account for an email, ignoring capitals. Old accounts may differ only in
    capitals: the exact spelling wins, otherwise the one whose password matches.
    """
    email = (email or '').strip()
    if not email:
        return None
    users = list(User.objects.filter(email__iexact=email))
    if len(users) <= 1:
        return users[0] if users else None
    exact = [u for u in users if u.email == email]
    if exact:
        return exact[0]
    if password is not None:
        for user in users:
            if user.check_password(password):
                return user
    return users[0]


def tokens_for(user) -> dict:
    """A fresh access + refresh pair (the refresh token is recorded so it can be blocked later)."""
    refresh = RefreshToken.for_user(user)
    refresh['role'] = user.role
    refresh['email'] = user.email
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def blacklist_refresh_tokens(user) -> int:
    """Blocks every refresh token of this user that is not blocked yet (signs out all devices)."""
    with transaction.atomic():
        tokens = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
        blocked = [BlacklistedToken(token=t) for t in tokens]
        BlacklistedToken.objects.bulk_create(blocked, ignore_conflicts=True)  # a parallel logout is fine
        return len(blocked)


def deactivate_user(user):
    """Soft delete: hidden, cannot sign in; email and ids stay taken (nothing is freed)."""
    user.deleted = True
    user.is_active = False
    user.save(update_fields=['deleted', 'is_active'])
    blacklist_refresh_tokens(user)


def restore_user(user):
    user.deleted = False
    user.is_active = True
    user.save(update_fields=['deleted', 'is_active'])


# ── Temporary passwords (admin import) ────────────────────────────────────────

TEMP_PASSWORD_LENGTH = 10
# No look-alikes (0/O, 1/l/I), so a password read off a screen is typed right.
TEMP_PASSWORD_ALPHABET = ''.join(c for c in string.ascii_letters + string.digits if c not in '0O1lI')
# Hashing hundreds of imported passwords with the full PBKDF2 work factor (1M rounds)
# would outlast the request timeout. Django re-hashes with the full work factor at the
# first sign-in. 10 random characters are far beyond guessing even with fewer rounds.
TEMP_PASSWORD_PBKDF2_ITERATIONS = 100_000


def temporary_password() -> str:
    while True:
        password = ''.join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))
        if any(c.isdigit() for c in password) and any(c.isalpha() for c in password):
            return password


def temporary_password_hash(raw) -> str:
    hasher = get_hasher('default')
    if isinstance(hasher, PBKDF2PasswordHasher):
        return hasher.encode(raw, hasher.salt(), iterations=TEMP_PASSWORD_PBKDF2_ITERATIONS)
    return make_password(raw)


# ── Password reset by email ───────────────────────────────────────────────────

def email_reset_enabled() -> bool:
    return bool(settings.EMAIL_HOST_USER)


def password_reset_link(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f'{settings.WEB_URL}/reset-password?uid={uid}&token={token}'


def send_password_reset_email(user) -> bool:
    link = password_reset_link(user)
    name = user.get_full_name() or user.email
    body = (
        f'Hello {name},\n\n'
        f'Someone (hopefully you) asked to reset the password of your {APP_NAME} account.\n'
        f'Open this link to choose a new password (it works once, for 1 day):\n\n'
        f'{link}\n\n'
        'If you did not ask for this, ignore this email: your password stays the same.\n'
    )
    try:
        send_mail(f'Reset your {APP_NAME} password', body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:
        logger.exception('Could not send the password reset email')
        return False
    return True


def user_from_reset_link(uid, token):
    """The account a reset link belongs to, or None if the link is invalid, used or expired."""
    try:
        pk = force_str(urlsafe_base64_decode(str(uid)))
        user = User.objects.get(pk=pk)
    except (TypeError, ValueError, OverflowError, DjangoValidationError, User.DoesNotExist):
        return None
    if not can_sign_in(user) or not default_token_generator.check_token(user, str(token)):
        return None
    return user
