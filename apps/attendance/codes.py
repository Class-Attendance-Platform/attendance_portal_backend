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
    now = time.time() if now is None else now
    expires_in = math.ceil((window(now) + 1) * PERIOD - now)
    return code_for(secret, window(now)), max(1, min(PERIOD, expires_in))

def code_matches(secret: str, code: str, now=None) -> bool:
    if not secret or not code or not code.isascii():
        return False
    counter = window(now)
    return any(hmac.compare_digest(code_for(secret, c), code) for c in (counter, counter - 1))

def check_in_url(session_id, code: str) -> str:
    return f'{settings.WEB_URL}/check-in?s={session_id}&c={code}'
