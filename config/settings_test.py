"""Settings for the test suite: no secrets, no network, in-memory SQLite."""

import os

os.environ.setdefault("SECRET_KEY", "test-only-secret-key")

from .settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

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
