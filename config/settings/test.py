"""
Settings for tests, makemigrations and local previews.

Uses a local SQLite file and an in-memory cache, so it never touches the
real (production) database or Redis, even when a .env file is present.

    python manage.py test --settings=config.settings.test
    python manage.py makemigrations --settings=config.settings.test
"""
import os

# base.py requires these; real values from .env are ignored below.
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key-do-not-use-in-production-0123456789')
for _name in ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST'):
    os.environ.setdefault(_name, 'unused')

from .base import *  # noqa: E402,F401,F403

DEBUG = True
ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # gitignored; tests use an in-memory copy
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

# Faster password hashing for tests and local previews only.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
