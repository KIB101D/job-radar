from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from bot import texts
from bot.telegram import TelegramError, get_client

ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]


class Command(BaseCommand):
    help = (
        "Point the Telegram bot at SITE_URL/telegram/webhook/ (with the secret header) "
        "and register the bot's / commands. Run once after each deploy that changes SITE_URL."
    )

    def add_arguments(self, parser):
        parser.add_argument("--info", action="store_true", help="Only show the current webhook.")
        parser.add_argument("--delete", action="store_true", help="Remove the webhook.")

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set.")
        client = get_client()

        try:
            if options["info"]:
                self._print_info(client.get_webhook_info())
                return
            if options["delete"]:
                client.delete_webhook()
                self.stdout.write(self.style.SUCCESS("Webhook removed."))
                return

            url = settings.SITE_URL + reverse("telegram_webhook")
            if not url.startswith("https://"):
                raise CommandError(
                    f"Telegram only calls HTTPS webhooks, got {url}. Set SITE_URL to your "
                    "public https:// address (for local testing use run_bot_locally instead)."
                )
            if not settings.TELEGRAM_WEBHOOK_SECRET:
                raise CommandError("TELEGRAM_WEBHOOK_SECRET is not set.")

            me = client.get_me()
            client.set_webhook(url, settings.TELEGRAM_WEBHOOK_SECRET, ALLOWED_UPDATES)
            client.set_my_commands(
                [{"command": name, "description": text} for name, text in texts.COMMANDS]
            )
        except TelegramError as exc:
            raise CommandError(f"Telegram API error: {exc}") from None

        self.stdout.write(self.style.SUCCESS(f"Webhook set for @{me['username']}: {url}"))
        if settings.TELEGRAM_BOT_USERNAME != me["username"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Set TELEGRAM_BOT_USERNAME={me['username']} so the site links to this bot."
                )
            )

    def _print_info(self, info: dict) -> None:
        self.stdout.write(f"URL: {info.get('url') or '(none)'}")
        self.stdout.write(f"Pending updates: {info.get('pending_update_count', 0)}")
        if info.get("last_error_message"):
            self.stdout.write(self.style.WARNING(f"Last error: {info['last_error_message']}"))
