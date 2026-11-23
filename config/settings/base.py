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
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    'apps.users',
    'apps.academic',
    'apps.attendance',
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
PORT = config('PORT', default=8000, cast=int)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT', default='5432'),
        'OPTIONS': {
            'sslmode': config('DB_SSLMODE', default='require'),
        },
    }
}

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'config.parsers.ObjectJSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
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
    'EXCEPTION_HANDLER': 'config.errors.api_exception_handler',
    'DEFAULT_THROTTLE_RATES': {
        'login': config('THROTTLE_LOGIN', default='10/min'),
        'login_ip': config('THROTTLE_LOGIN_IP', default='300/min'),
        'register': config('THROTTLE_REGISTER', default='10/hour'),
        'register_ip': config('THROTTLE_REGISTER_IP', default='300/hour'),
        'password_forgot': config('THROTTLE_PASSWORD_FORGOT', default='20/hour'),
        'password_change': config('THROTTLE_PASSWORD_CHANGE', default='10/hour'),
        'check_in': config('THROTTLE_CHECK_IN', default='30/min'),
        'face_register': config('THROTTLE_FACE_REGISTER', default='20/hour'),
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'CHECK_REVOKE_TOKEN': True,
    'TOKEN_REFRESH_SERIALIZER': 'apps.users.jwt.PasswordCheckedRefreshSerializer',
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'attendance-portal-cache',
    }
}

WEB_URL = config('WEB_URL', default='https://attendanceportal.sakibkx.tech').rstrip('/')
MIN_APP_VERSION = config('MIN_APP_VERSION', default='1.0.0')
LATEST_APP_VERSION = config('LATEST_APP_VERSION', default='1.0.0')
ATTENDANCE_MIN_PERCENT = config('ATTENDANCE_MIN_PERCENT', default=75, cast=int)

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 20
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default=f'HSTU Attendance Portal <{EMAIL_HOST_USER}>' if EMAIL_HOST_USER else 'webmaster@localhost',
)
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24
PASSWORD_RESET_EMAIL_INTERVAL = config('PASSWORD_RESET_EMAIL_INTERVAL', default=15 * 60, cast=int)
SEND_EMAIL_IN_BACKGROUND = True

CORS_ALLOW_ALL_ORIGINS = True
CORS_EXPOSE_HEADERS = ['Content-Disposition']

FACE_ENGINE = config('FACE_ENGINE', default='insightface')
FACE_MODEL_DIR = Path(config('FACE_MODEL_DIR', default=str(BASE_DIR / 'face_models')))
FACE_MATCH_THRESHOLD = config('FACE_MATCH_THRESHOLD', default=0.45, cast=float)
FACE_UNSURE_THRESHOLD = config('FACE_UNSURE_THRESHOLD', default=0.30, cast=float)
FACE_SAME_PERSON_THRESHOLD = config('FACE_SAME_PERSON_THRESHOLD', default=0.35, cast=float)
FACE_DUPLICATE_THRESHOLD = config('FACE_DUPLICATE_THRESHOLD', default=0.50, cast=float)
FACE_DETECTION_THRESHOLD = config('FACE_DETECTION_THRESHOLD', default=0.50, cast=float)
FACE_CLASS_PHOTO_MAX_SIDE = config('FACE_CLASS_PHOTO_MAX_SIDE', default=1920, cast=int)
DATA_UPLOAD_MAX_NUMBER_FILES = None
