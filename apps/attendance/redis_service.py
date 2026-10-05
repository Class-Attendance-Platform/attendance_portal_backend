"""
Redis-backed live session cache.

Key schema:
  session:{session_uuid}  →  JSON  {
      course_info_id : str,
      mode           : str,
      end_time       : float  (unix timestamp; extend moves it),
      submissions    : { "<student_id_int>": {"name": str, "profile_id": str,
                                              "method": "QR"|"CODE"|"TEACHER"|"FINGERPRINT",
                                              "device_id": str, "time": float} },
      devices        : { "<device_id>": "<student_id_int>" }   # one phone, one student per session
  }

Check-ins are accepted until end_time. The data is kept for a grace period
after that so the session can still be saved (see services.finalize_session)
even if the teacher stops it late or closes the app.
"""
import json
import threading
import time
from contextlib import contextmanager

from django.core.cache import cache
from redis.exceptions import LockError

PREFIX = 'session'
GRACE_SECONDS = 7 * 24 * 60 * 60

# add_submission results
ADDED = 'added'
CLOSED = 'closed'                      # session over (or its live data is gone)
ALREADY_CHECKED_IN = 'already_checked_in'
DEVICE_USED = 'device_used'            # this device already checked in another student

_local_lock = threading.Lock()  # used when the cache has no locks (tests, local preview)


class SessionBusy(Exception):
    """The session is being updated by many requests at once; try again."""


def _key(session_id: str) -> str:
    return f'{PREFIX}:{session_id}'


@contextmanager
def _lock(session_id: str):
    """
    Serialises read-modify-write of one session, so two students checking in
    at the same moment cannot overwrite each other's submission.
    Raises SessionBusy if the lock cannot be taken within a few seconds.
    """
    make_lock = getattr(cache, 'lock', None)  # django-redis provides this
    if make_lock is None:
        with _local_lock:
            yield
        return
    lock = make_lock(f'{_key(session_id)}:lock', timeout=10, sleep=0.02, blocking_timeout=5)
    if not lock.acquire():
        raise SessionBusy()
    try:
        yield
    finally:
        try:
            lock.release()
        except LockError:
            pass  # held longer than its timeout; the write itself already happened


def _save(session_id: str, data: dict):
    ttl = max(int(data['end_time'] - time.time()), 0) + GRACE_SECONDS
    cache.set(_key(session_id), json.dumps(data), timeout=ttl)


def create_session_cache(session_id: str, course_info_id: str, mode: str, duration_seconds: int):
    data = {
        'course_info_id': course_info_id,
        'mode': mode,
        'end_time': time.time() + duration_seconds,
        'submissions': {},
        'devices': {},
    }
    cache.set(_key(session_id), json.dumps(data), timeout=duration_seconds + GRACE_SECONDS)


def get_session_cache(session_id: str) -> dict | None:
    """The live session data, also after end_time (None once saved or gone)."""
    raw = cache.get(_key(session_id))
    if raw is None:
        return None
    return json.loads(raw)


def is_open(data: dict | None) -> bool:
    """True while the session still accepts check-ins."""
    return bool(data) and time.time() <= data['end_time']


def add_submission(session_id: str, student_int_id: int, student_name: str, method: str,
                   device_id: str = '', profile_id: str = '') -> str:
    """
    Records a check-in. Returns ADDED, CLOSED, ALREADY_CHECKED_IN or DEVICE_USED (a
    `device_id` that already checked in a different student in this session).
    Raises SessionBusy when too many check-ins arrive at once.
    """
    with _lock(session_id):
        data = get_session_cache(session_id)
        if not is_open(data):
            return CLOSED
        key = str(student_int_id)
        if key in data['submissions']:
            return ALREADY_CHECKED_IN
        devices = data.setdefault('devices', {})
        if device_id and devices.get(device_id, key) != key:
            return DEVICE_USED
        data['submissions'][key] = {
            'name': student_name,
            'profile_id': str(profile_id),
            'method': method,
            'device_id': device_id,
            'time': time.time(),
        }
        if device_id:
            devices[device_id] = key
        _save(session_id, data)
        return ADDED


def extend_session(session_id: str, seconds: int) -> float | None:
    """Moves end_time later; returns the new end_time (None if the session is over or gone)."""
    with _lock(session_id):
        data = get_session_cache(session_id)
        if not is_open(data):
            return None
        data['end_time'] += seconds
        _save(session_id, data)
        return data['end_time']


def get_submissions(session_id: str) -> dict:
    data = get_session_cache(session_id)
    return data['submissions'] if data else {}


def close_session(session_id: str) -> dict:
    """
    Stops new check-ins and returns the submissions. The data stays until
    delete_session_cache(), so nothing is lost if saving them fails.
    """
    with _lock(session_id):
        data = get_session_cache(session_id)
        if not data:
            return {}
        if is_open(data):
            data['end_time'] = time.time() - 1
            cache.set(_key(session_id), json.dumps(data), timeout=GRACE_SECONDS)
        return data['submissions']


def time_left(session_id: str) -> int:
    data = get_session_cache(session_id)
    if not data:
        return 0
    return max(0, int(data['end_time'] - time.time()))


def delete_session_cache(session_id: str):
    cache.delete(_key(session_id))


def discard_session(session_id: str):
    """Deletes a cancelled session's data (under the lock, so a late check-in cannot write it back)."""
    try:
        with _lock(session_id):
            delete_session_cache(session_id)
    except SessionBusy:
        delete_session_cache(session_id)
