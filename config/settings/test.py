import os

os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-do-not-use-in-production-0123456789')
for _name in ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST'):
    os.environ.setdefault(_name, 'unused')

from .base import *  # noqa: E402,F401,F403

DEBUG = True
ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_HOST_USER = 'portal@example.com'
EMAIL_HOST_PASSWORD = ''
DEFAULT_FROM_EMAIL = 'HSTU Attendance Portal <portal@example.com>'
SEND_EMAIL_IN_BACKGROUND = False
WEB_URL = 'http://localhost:8081'
