"""Project wiring: dependencies, production settings on Vercel, the Vercel build step."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_and_requirements_pin_the_same_packages():
    """Vercel installs from pyproject.toml, CI and local setup from requirements.txt."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert sorted(pyproject) == sorted(requirements)


def production_settings(**extra_env) -> dict:
    """Import config.settings as production would see it, in a clean process."""
    env = {
        "PATH": os.environ["PATH"],
        "READ_DOT_ENV": "False",  # a developer's local .env must not leak in
        "DEBUG": "False",
        "SECRET_KEY": "x" * 50,
        "DATABASE_URL": "postgres://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/db?sslmode=require",
        **extra_env,
    }
    code = (
        "import json, os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings';"
        "from django.conf import settings as s;"
        "print(json.dumps({'hosts': s.ALLOWED_HOSTS, 'csrf': s.CSRF_TRUSTED_ORIGINS,"
        " 'site': s.SITE_URL, 'conn': s.DATABASES['default']['CONN_MAX_AGE'],"
        " 'cursors': s.DATABASES['default'].get('DISABLE_SERVER_SIDE_CURSORS')}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_vercel_hosts_site_url_and_connections():
    s = production_settings(
        VERCEL="1",
        VERCEL_PROJECT_PRODUCTION_URL="job-radar.vercel.app",
        VERCEL_URL="job-radar-abc123.vercel.app",
    )
    assert {"job-radar.vercel.app", "job-radar-abc123.vercel.app"} <= set(s["hosts"])
    assert "https://job-radar.vercel.app" in s["csrf"]
    assert s["site"] == "https://job-radar.vercel.app"
    assert s["conn"] == 0  # no persistent connections from serverless functions
    assert s["cursors"] is True  # Neon's PgBouncer pooler


def test_explicit_site_url_wins():
    s = production_settings(
        VERCEL="1",
        VERCEL_PROJECT_PRODUCTION_URL="x.vercel.app",
        SITE_URL="https://radar.example.com/",
    )
    assert s["site"] == "https://radar.example.com"


def run_build(monkeypatch, vercel_env):
    sys.path.insert(0, str(ROOT / "bin"))
    try:
        import vercel_build
    finally:
        sys.path.pop(0)
    calls = []
    monkeypatch.setenv("VERCEL_ENV", vercel_env)
    monkeypatch.setattr(
        "django.core.management.call_command", lambda name, **kw: calls.append(name)
    )
    assert vercel_build.main() == 0
    return calls


@pytest.mark.parametrize("vercel_env", ["preview", "development", ""])
def test_build_leaves_database_and_bot_alone_outside_production(monkeypatch, vercel_env):
    assert run_build(monkeypatch, vercel_env) == []


def test_production_build_migrates_and_sets_the_webhook(monkeypatch, settings):
    settings.TELEGRAM_BOT_TOKEN = "123:abc"
    assert run_build(monkeypatch, "production") == ["migrate", "set_telegram_webhook"]
