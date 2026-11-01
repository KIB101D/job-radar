"""
Django settings for Job Radar.

Everything environment-specific comes from environment variables (optionally
loaded from a local .env file). Nothing secret is hardcoded: in production
(DEBUG=False) a missing SECRET_KEY fails fast instead of silently using a default.
"""

from pathlib import Path

import dj_database_url
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)

DEBUG = env.bool("DEBUG", default=False)

if DEBUG:
    SECRET_KEY = env("SECRET_KEY", default="django-insecure-local-dev-only")
else:
    SECRET_KEY = env("SECRET_KEY")  # raises ImproperlyConfigured if missing

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "radar",
    "subscriptions",
    "bot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "subscriptions.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite locally, Postgres (Neon) in production via DATABASE_URL.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=env.int("DB_CONN_MAX_AGE", default=60),
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uk"
TIME_ZONE = "Europe/Kyiv"  # display only; datetimes are stored in UTC
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Job Radar ---------------------------------------------------------------

# Public base URL of the site, used in bot messages ("Змінити фільтри" link).
SITE_URL = env("SITE_URL", default="http://localhost:8000").rstrip("/")

# Telegram bot. The webhook is SITE_URL + /telegram/webhook/; Telegram sends
# TELEGRAM_WEBHOOK_SECRET in a header so we know the request is really from it.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")

# The pop-up "N нових вакансій" toast (templates/components/toast.html) is built
# but switched off for now; the "Нові · N" tab is the live indicator instead.
NEW_VACANCIES_TOAST = env.bool("NEW_VACANCIES_TOAST", default=False)

# Polite fetching of RSS feeds.
FETCH_USER_AGENT = env(
    "FETCH_USER_AGENT",
    default="JobRadar/0.1 (+https://github.com/KIB101D/job-radar)",
)
FETCH_TIMEOUT_SECONDS = env.float("FETCH_TIMEOUT_SECONDS", default=15.0)
FETCH_MAX_RETRIES = env.int("FETCH_MAX_RETRIES", default=3)
FETCH_DELAY_BETWEEN_SOURCES_SECONDS = env.float("FETCH_DELAY_BETWEEN_SOURCES_SECONDS", default=2.0)
# check_active opens vacancy pages one by one; pause between them and cap a run.
CHECK_ACTIVE_DELAY_SECONDS = env.float("CHECK_ACTIVE_DELAY_SECONDS", default=1.5)
CHECK_ACTIVE_MAX_PER_RUN = env.int("CHECK_ACTIVE_MAX_PER_RUN", default=300)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "httpx": {"level": "WARNING"},
    },
}
