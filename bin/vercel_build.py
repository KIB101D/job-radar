"""
Vercel build step, after dependencies are installed (collectstatic is run by
Vercel itself). For a production deployment: apply migrations to Neon and point
the Telegram webhook at this deploy. Anything else (a preview, a local
`vercel build`) touches neither the shared database nor the bot.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the project root


def main() -> int:
    env = os.environ.get("VERCEL_ENV", "")
    if env != "production":
        print(f"VERCEL_ENV={env or '(unset)'}: skipping migrations and the Telegram webhook.")
        return 0

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django
    from django.conf import settings
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False)

    if settings.TELEGRAM_BOT_TOKEN:
        try:
            call_command("set_telegram_webhook")
        except Exception as exc:  # the site works without Telegram: don't fail the deploy
            print(f"WARNING: set_telegram_webhook failed ({exc}). See docs/deploy.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
