"""Hardened, env-driven settings. Production requires explicit secrets; nothing sensitive has a default."""
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = sys.argv[1:2] == ["test"]          # only `manage.py test`, never a substring match


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return str(env(key, str(default))).lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "insecure-dev-only-key-" + "x" * 40
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DEBUG is off.")

ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h]
if not ALLOWED_HOSTS and not (DEBUG or TESTING):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required when DEBUG is off.")
CSRF_TRUSTED_ORIGINS = [o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "apps.core",
    "apps.accounts",
    "apps.academics",
    "apps.integrations",
    "apps.courses",
    "apps.exams",
    "apps.liveclass",
    "apps.reports",
    "apps.coursework",
    "apps.attendance",
    "apps.timetable",
    "apps.schoolcal",
    "apps.appeals",
    "apps.transfers",
    "apps.files",
    "apps.imports",
    "apps.sms",
    "apps.sysadmin",
    "apps.evaluation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    # project templates first: they override the admin base template (modern skin)
    "DIRS": [BASE_DIR / "apps" / "core" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

AUTH_USER_MODEL = "accounts.User"

# --- Database: talk to PgBouncer (transaction pooling) in production -------------------------
if env("DB_NAME"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", "pgbouncer"),
        "PORT": env("DB_PORT", "6432"),
        "CONN_MAX_AGE": 0,                      # PgBouncer owns pooling
        "DISABLE_SERVER_SIDE_CURSORS": True,    # required for transaction pooling
        "CONN_HEALTH_CHECKS": False,
        "OPTIONS": {"connect_timeout": 5, "sslmode": env("DB_SSLMODE", "prefer")},
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# --- Cache / sessions ---------------------------------------------------------------------
if env("REDIS_CACHE_URL"):
    CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL"),
        "OPTIONS": {"socket_timeout": 1, "socket_connect_timeout": 1},
        "KEY_PREFIX": "lms",
    }}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_ENGINE = "django.contrib.sessions.backends.cache"   # only the admin uses sessions

# --- Auth ---------------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.auth.FastJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # JSON for the API; multipart/form-data for file uploads (imports, photos, documents)
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser", "rest_framework.parsers.MultiPartParser",
                               "rest_framework.parsers.FormParser"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.BoundedPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min", "user": "240/min",
        "login": "10/min", "exam": "60/min", "join": "20/min", "report": "20/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.safe_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=10),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=12),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,       # avoids a write on every login during login storms
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY") or (SECRET_KEY if (DEBUG or TESTING) else ""),
}

LANGUAGE_CODE = "fa"
PRIVATE_MEDIA_ROOT = env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media"))
SMS_ENCRYPTION_KEY = env("SMS_ENCRYPTION_KEY", "")
if not (DEBUG or TESTING) and not SMS_ENCRYPTION_KEY:
    raise ImproperlyConfigured("SMS_ENCRYPTION_KEY is required in production.")
MFA_ENFORCE = env_bool("MFA_ENFORCE", not (DEBUG or TESTING))   # admin roles need a second factor in production
if not (DEBUG or TESTING) and (not env("PRIVATE_MEDIA_ROOT") or not env("JWT_SIGNING_KEY")):
    raise ImproperlyConfigured("PRIVATE_MEDIA_ROOT and JWT_SIGNING_KEY are required in production.")
UPLOAD_MAX_BYTES = int(env("UPLOAD_MAX_BYTES", str(100 * 1024 * 1024)))
APPEAL_WINDOW_DAYS = int(env("APPEAL_WINDOW_DAYS", "7"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024   # files never pass through Django; API bodies are tiny

ADMIN_URL = env("DJANGO_ADMIN_URL", "admin/")
# The system panel (/system/) replaces the Django admin; set DJANGO_ADMIN_ENABLED=0 to remove the admin route entirely.
ADMIN_ENABLED = env_bool("DJANGO_ADMIN_ENABLED", True)

# --- Transport security -------------------------------------------------------------------
if not (DEBUG or TESTING):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")   # set only behind a trusted proxy
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# --- Celery -------------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://redis-broker:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {"apps.exams.tasks.*": {"queue": "grading"}}
CELERY_BEAT_SCHEDULE = {
    "auto-submit-expired-attempts": {"task": "apps.exams.tasks.auto_submit_expired", "schedule": 60.0},
}
if TESTING:
    CELERY_TASK_ALWAYS_EAGER = True

# --- Live-class providers (secrets only from environment / secret manager) ---------------------
BBB_URL = env("BBB_URL", "")
BBB_SECRET = env("BBB_SECRET", "")

# --- Logging ------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"format": '{"t":"%(asctime)s","lvl":"%(levelname)s","log":"%(name)s","msg":"%(message)s"}'}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
