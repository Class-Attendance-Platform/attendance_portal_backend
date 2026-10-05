from pathlib import Path
from datetime import timedelta
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('SECRET_KEY')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_extensions',
    # Third party
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    # Local apps
    'apps.users',
    'apps.academic',
    'apps.attendance',
    'apps.hardware',
    'apps.reports',
    'apps.faces',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT', default='5432'),
        'OPTIONS': {
            # "require" for hosted databases (Neon); "disable" for PostgreSQL on the same server
            'sslmode': config('DB_SSLMODE', default='require'),
        },
    }
}

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── DRF ──────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    # Every error carries a readable "message" (config/errors.py)
    'EXCEPTION_HANDLER': 'config.errors.api_exception_handler',
    # Per IP (login, register, password_forgot) or per user (check_in); see apps/users/throttles.py.
    # Classes behind one shared campus IP may need higher limits: raise them in .env.
    'DEFAULT_THROTTLE_RATES': {
        'login': config('THROTTLE_LOGIN', default='10/min'),
        'register': config('THROTTLE_REGISTER', default='5/hour'),
        'password_forgot': config('THROTTLE_PASSWORD_FORGOT', default='5/hour'),
        'check_in': config('THROTTLE_CHECK_IN', default='30/min'),
    },
}

# ── JWT ───────────────────────────────────────────────────────────────────────
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            # TLS (rediss://, e.g. Upstash) needs this; a plain local redis:// must not get it
            'CONNECTION_POOL_KWARGS': {'ssl_cert_reqs': None} if REDIS_URL.startswith('rediss://') else {},
        }
    }
}

# ── App ───────────────────────────────────────────────────────────────────────
# Public address of the web app: links in emails and QR codes (no trailing slash)
WEB_URL = config('WEB_URL', default='https://attendanceportal.sakibkx.tech').rstrip('/')
# The Android app blocks with "Please update" below MIN_APP_VERSION (GET /api/config/app/)
MIN_APP_VERSION = config('MIN_APP_VERSION', default='1.0.0')
LATEST_APP_VERSION = config('LATEST_APP_VERSION', default='1.0.0')
ATTENDANCE_MIN_PERCENT = config('ATTENDANCE_MIN_PERCENT', default=75, cast=int)

# ── Email (Gmail SMTP; password reset links) ──────────────────────────────────
# Empty EMAIL_HOST_USER = password reset by email is off (admins reset passwords instead).
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 20
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')  # Gmail app password
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default=f'HSTU Attendance Portal <{EMAIL_HOST_USER}>' if EMAIL_HOST_USER else 'webmaster@localhost',
)
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # reset links work for 1 day

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True  # Tighten in production

# ── Face attendance ───────────────────────────────────────────────────────────
# Engine "insightface" = InsightFace buffalo_l models via onnxruntime
# (download with `python manage.py download_face_models`).
FACE_ENGINE = config('FACE_ENGINE', default='insightface')
FACE_MODEL_DIR = Path(config('FACE_MODEL_DIR', default=str(BASE_DIR / 'face_models')))
# Similarity scores run from -1 to 1 (same person usually 0.6+, strangers below 0.2).
FACE_MATCH_THRESHOLD = config('FACE_MATCH_THRESHOLD', default=0.45, cast=float)        # present
FACE_UNSURE_THRESHOLD = config('FACE_UNSURE_THRESHOLD', default=0.30, cast=float)      # teacher checks
FACE_SAME_PERSON_THRESHOLD = config('FACE_SAME_PERSON_THRESHOLD', default=0.35, cast=float)  # 3 sign-up shots
FACE_DUPLICATE_THRESHOLD = config('FACE_DUPLICATE_THRESHOLD', default=0.50, cast=float)  # face already taken
FACE_DETECTION_THRESHOLD = config('FACE_DETECTION_THRESHOLD', default=0.50, cast=float)
FACE_CLASS_PHOTO_MAX_SIDE = config('FACE_CLASS_PHOTO_MAX_SIDE', default=1920, cast=int)
