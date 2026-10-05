"""
Error responses: every error carries a readable `message` (API v2 conventions).

    {"success": false, "message": "<one readable sentence>", "code": "<machine_code>"?,
     "errors": {"field": ["msg"]}?}

- `api_exception_handler` (REST_FRAMEWORK['EXCEPTION_HANDLER']) covers raised errors:
  validation, not signed in, no permission, not found, throttled, ...
- Views build their own errors with `error_response()` and `validation_error_response()`.
- `json_not_found` / `json_server_error` (config/urls.py) cover URLs outside DRF.
"""
import math
import re

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, JsonResponse
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

# Plain names for field names that would read badly with the default
# ("student_id" -> "Student ID", not "Student id").
FIELD_LABELS = {
    'email': 'Email',
    'password': 'Password',
    'new_password': 'New password',
    'current_password': 'Current password',
    'first_name': 'First name',
    'last_name': 'Last name',
    'student_id': 'Student ID',
    'employee_id': 'Employee ID',
    'current_level': 'Level',
    'current_semester': 'Term',
    'semester_id': 'Semester',
    'target_semester_id': 'Semester',
    'course_id': 'Course',
    'teacher_id': 'Teacher',
    'course_info_id': 'Course',
    'profile_id': 'Student',
    'profile_ids': 'Students',
    'present_profile_ids': 'Present students',
    'session_id': 'Session',
    'device_id': 'Device ID',
    'duration_minutes': 'Length',
    'uid': 'Link',
    'token': 'Link',
    'file': 'File',
}
NO_LABEL_FIELDS = {'non_field_errors', 'detail', '__all__'}


def field_label(name) -> str:
    name = str(name)
    if name in FIELD_LABELS:
        return FIELD_LABELS[name]
    return name.replace('_', ' ').strip().capitalize() or 'This field'


def _sentence(text: str) -> str:
    text = str(text).strip()
    if text and text[-1] not in '.!?':
        text += '.'
    return text


def _first_error(errors, field=None):
    """(field name, message) of the first error in a DRF error structure."""
    if isinstance(errors, dict):
        for key, value in errors.items():
            found = _first_error(value, key)
            if found[1]:
                return found
        return field, None
    if isinstance(errors, (list, tuple)):
        for item in errors:
            found = _first_error(item, field)
            if found[1]:
                return found
        return field, None
    if errors is None or str(errors).strip() == '':
        return field, None
    return field, str(errors)


def readable_field_error(field, message) -> str:
    """One field error as a plain sentence: "Student ID is required.", never "student_id: ..."."""
    message = str(message).strip()
    if field is None or isinstance(field, int) or str(field) in NO_LABEL_FIELDS:
        return _sentence(message)
    label = field_label(field)
    fixed = {
        'This field is required.': f'{label} is required.',
        'This field may not be blank.': f'{label} is required.',
        'This field may not be null.': f'{label} is required.',
        'A valid integer is required.': f'{label} must be a whole number.',
        'A valid number is required.': f'{label} must be a number.',
        'Not a valid string.': f'{label} must be text.',
        'Must be a valid UUID.': f'{label} is not valid.',
        'Must be a valid boolean.': f'{label} must be true or false.',
        'This list may not be empty.': f'{label}: choose at least one.',
    }
    if message in fixed:
        return fixed[message]
    if message.endswith('is not a valid choice.'):
        return message.replace('valid choice', f'valid {label.lower()}')
    if 'This field' in message:
        return _sentence(message.replace('This field', label))
    if 'this field' in message:
        return _sentence(message.replace('this field', label.lower()))
    if 'This value' in message:
        return _sentence(message.replace('This value', label))
    return _sentence(message)


def first_error_message(errors, default='Please check the form and try again.') -> str:
    field, message = _first_error(errors)
    if not message:
        return default
    return readable_field_error(field, message)


def error_response(message, status=400, code=None, errors=None, **extra):
    body = {'success': False, 'message': message}
    if code:
        body['code'] = code
    if errors:
        body['errors'] = errors
    body.update(extra)
    return Response(body, status=status)


def validation_error_response(errors, status=400, code=None, message=None, **extra):
    """400 with field errors; `message` repeats the first one in plain words."""
    if not isinstance(errors, dict):
        errors = {'non_field_errors': errors if isinstance(errors, list) else [errors]}
    return error_response(message or first_error_message(errors), status=status, code=code, errors=errors, **extra)


def throttled_message(wait) -> str:
    if not wait:
        return 'Too many attempts. Please try again later.'
    seconds = math.ceil(wait)
    if seconds <= 90:
        return f'Too many attempts. Please try again in {seconds} seconds.'
    minutes = math.ceil(seconds / 60)
    return f'Too many attempts. Please try again in {minutes} minutes.'


# Friendlier words for DRF / SimpleJWT defaults (only used when the detail is not custom).
DEFAULT_MESSAGES = {
    'not_authenticated': 'Please sign in to continue.',
    'token_not_valid': 'Your sign-in has expired. Please sign in again.',
    'user_not_found': 'This account no longer exists.',
    'user_inactive': 'This account has been disabled. Contact the department office.',
    'permission_denied': 'You do not have permission to do this.',
    'not_found': 'This item was not found.',
}


def _codes(exc):
    try:
        codes = exc.get_codes()
    except Exception:  # pragma: no cover - defensive
        return None
    if isinstance(codes, str):
        return codes
    if isinstance(codes, dict):  # SimpleJWT: {'detail': ..., 'code': 'token_not_valid', ...}
        code = codes.get('code')
        if isinstance(code, str):
            return code
        if isinstance(code, list) and code:
            return str(code[0])
    return None


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # not an API error: Django answers 500 (json_server_error)

    data = response.data
    if isinstance(exc, exceptions.ValidationError):
        errors = data if isinstance(data, dict) else {'non_field_errors': data if isinstance(data, list) else [data]}
        response.data = {'success': False, 'message': first_error_message(errors), 'errors': errors}
        return response

    body = dict(data) if isinstance(data, dict) else {'detail': data}
    if isinstance(exc, exceptions.Throttled):
        message, code = throttled_message(exc.wait), 'throttled'
    elif isinstance(exc, Http404):
        # get_object_or_404 says "No StudentProfile matches the given query."
        message, code = DEFAULT_MESSAGES['not_found'], 'not_found'
    elif isinstance(exc, DjangoPermissionDenied):
        message, code = DEFAULT_MESSAGES['permission_denied'], 'permission_denied'
    else:
        code = _codes(exc) if isinstance(exc, exceptions.APIException) else None
        detail = body.get('detail')
        is_default = isinstance(exc, exceptions.APIException) and str(detail) == str(exc.default_detail)
        if code in DEFAULT_MESSAGES and (is_default or code in ('token_not_valid', 'user_not_found', 'user_inactive')):
            message = DEFAULT_MESSAGES[code]
        elif detail:
            message = _sentence(re.sub(r'\s+', ' ', str(detail)))
        else:
            message = first_error_message(body, default='Something went wrong. Please try again.')
    body.update({'success': False, 'message': message})
    if code and 'code' not in body:
        body['code'] = code
    response.data = body
    return response


def json_not_found(request, exception=None):
    return JsonResponse(
        {'success': False, 'message': 'This address does not exist.', 'code': 'not_found'}, status=404
    )


def json_server_error(request):
    return JsonResponse(
        {'success': False, 'message': 'Something went wrong on the server. Please try again.',
         'code': 'server_error'},
        status=500,
    )
