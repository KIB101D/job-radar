"""Settings for the test suite: no secrets, no network, in-memory SQLite."""

import os

os.environ.setdefault("SECRET_KEY", "test-only-secret-key")

import dj_database_url  # noqa: E402

from .settings import *  # noqa: E402,F403

# In-memory SQLite by default. CI also runs the suite on Postgres (what Neon runs)
# by setting TEST_DATABASE_URL; pytest-django creates and drops a test_ database.
_test_db = os.environ.get("TEST_DATABASE_URL")
DATABASES = {
    "default": dj_database_url.parse(_test_db)
    if _test_db
    else {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SITE_URL = "https://radar.test"
TELEGRAM_BOT_TOKEN = "123:test-token"
TELEGRAM_BOT_USERNAME = "job_radar_test_bot"
TELEGRAM_WEBHOOK_SECRET = "test-webhook-secret"

# Never sleep between sources or retries in tests.
FETCH_DELAY_BETWEEN_SOURCES_SECONDS = 0
CHECK_ACTIVE_DELAY_SECONDS = 0

# WhiteNoise only matters for collected static files in production.
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]  # noqa: F405
