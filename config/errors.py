from django.http import JsonResponse
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def _first_message(errors, default='Please check the form and try again.'):
    if isinstance(errors, dict):
        for value in errors.values():
            msg = _first_message(value, None)
            if msg:
                return msg
    elif isinstance(errors, (list, tuple)):
        for item in errors:
            msg = _first_message(item, None)
            if msg:
                return msg
    elif errors:
        return str(errors)
    return default


def error_response(message, status=400, code=None, errors=None, **extra):
    body = {'success': False, 'message': message}
    if code:
        body['code'] = code
    if errors:
        body['errors'] = errors
    body.update(extra)
    return Response(body, status=status)


def validation_error_response(errors, status=400, code=None, message=None, **extra):
    if not isinstance(errors, dict):
        errors = {'non_field_errors': errors if isinstance(errors, list) else [errors]}
    return error_response(message or _first_message(errors), status=status, code=code, errors=errors, **extra)


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, exceptions.ValidationError):
        return validation_error_response(response.data, status=response.status_code)
    data = response.data
    message = str(data['detail']) if isinstance(data, dict) and 'detail' in data else _first_message(data, 'Something went wrong.')
    body = dict(data) if isinstance(data, dict) else {'detail': data}
    body.update({'success': False, 'message': message})
    response.data = body
    return response


def json_not_found(request, exception=None):
    return JsonResponse({'success': False, 'message': 'Not found.', 'code': 'not_found'}, status=404)


def json_server_error(request):
    return JsonResponse({'success': False, 'message': 'Internal server error.', 'code': 'server_error'}, status=500)
