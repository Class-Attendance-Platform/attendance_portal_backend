"""
The rotating 6-digit check-in code of a live session (API v2 section 5).

Every PERIOD seconds there is a new code: HMAC-SHA256 of the window number
floor(unix / PERIOD) (8 bytes, big-endian) keyed with the session's secret
`qr_token`, cut to 6 digits like a one-time password (RFC 4226 dynamic truncation),
zero-padded. The current and the previous window are accepted, so a code works for
30-60 seconds. The secret never leaves the server; only codes do.
"""
import hashlib
import hmac
import math
import time

from django.conf import settings

PERIOD = 30
DIGITS = 6


def window(now=None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def code_for(secret: str, counter: int) -> str:
    digest = hmac.new(secret.encode(), counter.to_bytes(8, 'big'), hashlib.sha256).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset:offset + 4], 'big') & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_code(secret: str, now=None) -> tuple[str, int]:
    """(code, seconds until it changes)."""
    now = time.time() if now is None else now
    expires_in = math.ceil((window(now) + 1) * PERIOD - now)
    return code_for(secret, window(now)), max(1, min(PERIOD, expires_in))


def code_matches(secret: str, code: str, now=None) -> bool:
    """True if `code` is the current or the previous window's code."""
    if not secret or not code or not code.isascii():
        return False  # compare_digest raises on non-ASCII text
    counter = window(now)
    return any(hmac.compare_digest(code_for(secret, c), code) for c in (counter, counter - 1))


def check_in_url(session_id, code: str) -> str:
    """What the QR shows: the web check-in page (the in-app scanner reads `s` and `c` too)."""
    return f'{settings.WEB_URL}/check-in?s={session_id}&c={code}'
