"""
Production settings (the VPS). Every value comes from .env; see .env.example.
Run with DJANGO_SETTINGS_MODULE=config.settings.production (the systemd service sets it).
"""
from decouple import Csv, config

from .base import *  # noqa: F401,F403
from .base import MIDDLEWARE

DEBUG = False

ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv())

# Only the web app may call the API from a browser. The Android app sends no
# Origin header and the desktop app loads the web app, so they need nothing here.
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', cast=Csv(), default='')
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', cast=Csv(), default='')  # for /admin/

# nginx terminates HTTPS and forwards the original scheme
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)

# Static files (Django admin) served by the app itself
MIDDLEWARE = [
    MIDDLEWARE[0],  # SecurityMiddleware first
    'whitenoise.middleware.WhiteNoiseMiddleware',
    *MIDDLEWARE[1:],
]
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# Errors go to the service log (journalctl -u attendanceportal-api)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'WARNING'},
}
