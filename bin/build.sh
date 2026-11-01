#!/usr/bin/env bash
# Render build step: dependencies, static files, database schema, Telegram webhook.
# (Render's free plan has no pre-deploy hook, so migrations run here.)
set -o errexit -o nounset -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --no-input
python manage.py migrate --no-input

# Point the bot at this deploy and register its commands. Idempotent, so it runs
# on every deploy; not fatal, because the site itself works without Telegram.
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  python manage.py set_telegram_webhook \
    || echo "WARNING: set_telegram_webhook failed, see docs/deploy.md (Telegram webhook)."
fi
