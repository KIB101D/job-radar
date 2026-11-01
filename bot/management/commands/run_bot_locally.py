"""
DEVELOPMENT ONLY: talk to the bot from your laptop without a public HTTPS URL.

Production uses the webhook (Telegram pushes updates to /telegram/webhook/).
Locally there's no public address for Telegram to call, so this command pulls
updates with getUpdates instead and feeds them to the very same handlers.
Telegram doesn't allow getUpdates while a webhook is set, so it is removed
first; run set_telegram_webhook again before going back to production.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.handlers import safe_handle_update
from bot.management.commands.set_telegram_webhook import ALLOWED_UPDATES
from bot.telegram import TelegramError, get_client


class Command(BaseCommand):
    help = "DEV ONLY: run the bot with long polling instead of the webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Handle one batch of updates and exit (tests)."
        )

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set.")
        client = get_client()
        try:
            me = client.get_me()
            if client.get_webhook_info().get("url"):
                client.delete_webhook()
                self.stdout.write(
                    self.style.WARNING(
                        "Webhook removed for local polling. "
                        "Run set_telegram_webhook again for production."
                    )
                )
        except TelegramError as exc:
            raise CommandError(f"Telegram API error: {exc}") from None

        self.stdout.write(self.style.SUCCESS(f"@{me['username']} is listening. Ctrl+C to stop."))
        offset = None
        while True:
            try:
                updates = client.get_updates(offset, ALLOWED_UPDATES)
            except TelegramError as exc:
                self.stderr.write(f"getUpdates failed: {exc}; retrying in 5 s")
                time.sleep(5)
                continue
            except KeyboardInterrupt:
                return
            for update in updates:
                offset = update["update_id"] + 1
                safe_handle_update(update)
            if options["once"]:
                return
