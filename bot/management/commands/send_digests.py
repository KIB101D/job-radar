from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot.digests import send_due_digests
from radar.locks import job_lock

DIGEST_LOCK = "send_digests"
DIGEST_LOCK_TTL = timedelta(minutes=10)


class Command(BaseCommand):
    help = "Send due Telegram digests. Run by cron every 30 min, after the fetch."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set.")

        with job_lock(DIGEST_LOCK, DIGEST_LOCK_TTL) as acquired:
            if not acquired:
                self.stdout.write("Another digest run is in progress, skipping.")
                return
            run = send_due_digests()

        self.stdout.write(
            f"Checked {run.checked} subscriptions: {run.due} due, {run.sent} sent, "
            f"{run.empty} with nothing new, {run.unlinked} unlinked, {run.failed} failed."
        )
        for error in run.errors:
            self.stderr.write(error)
        # Fail the workflow only when every due digest failed (e.g. Telegram is down);
        # a single blocked chat is routine and shouldn't turn the run red.
        if run.failed and run.failed == run.due:
            raise CommandError("All due digests failed.")
