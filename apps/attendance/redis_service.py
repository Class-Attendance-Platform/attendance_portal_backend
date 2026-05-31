"""
Redis-backed live session cache.

Key schema:
  session:{session_uuid}  →  JSON  {
      course_info_id : str,
      mode           : str,
      end_time       : float  (unix timestamp),
      submissions    : { "<student_id_int>": {"name": str, "mac": str} }
  }
TTL = duration_seconds + 120 buffer
"""
import json
import time
from django.core.cache import cache

PREFIX = 'session'


def _key(session_id: str) -> str:
    return f'{PREFIX}:{session_id}'


def create_session_cache(session_id: str, course_info_id: str, mode: str, duration_seconds: int):
    data = {
        'course_info_id': course_info_id,
        'mode': mode,
        'end_time': time.time() + duration_seconds,
        'submissions': {},
    }
    cache.set(_key(session_id), json.dumps(data), timeout=duration_seconds + 120)


def get_session_cache(session_id: str) -> dict | None:
    raw = cache.get(_key(session_id))
    if raw is None:
        return None
    data = json.loads(raw)
    # Auto-expire if past end_time
    if time.time() > data['end_time']:
        cache.delete(_key(session_id))
        return None
    return data


def add_submission(session_id: str, student_int_id: int, student_name: str, mac: str) -> bool:
    """
    Returns True if successfully added, False if session not found / already submitted.
    """
    raw = cache.get(_key(session_id))
    if not raw:
        return False
    data = json.loads(raw)
    if time.time() > data['end_time']:
        cache.delete(_key(session_id))
        return False
    key = str(student_int_id)
    if key in data['submissions']:
        return False   # already submitted
    data['submissions'][key] = {'name': student_name, 'mac': mac}
    ttl = max(int(data['end_time'] - time.time()) + 120, 60)
    cache.set(_key(session_id), json.dumps(data), timeout=ttl)
    return True


def get_submissions(session_id: str) -> dict:
    data = get_session_cache(session_id)
    return data['submissions'] if data else {}


def time_left(session_id: str) -> int:
    data = get_session_cache(session_id)
    if not data:
        return 0
    return max(0, int(data['end_time'] - time.time()))


def delete_session_cache(session_id: str):
    cache.delete(_key(session_id))
