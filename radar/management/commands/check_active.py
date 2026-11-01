from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from radar.locks import job_lock
from radar.services.activity import check_active

LOCK = "check_active"
LOCK_TTL = timedelta(hours=2)


class Command(BaseCommand):
    help = (
        "Open pages of vacancies that left the RSS feed and mark closed ones inactive "
        "(404/410 or 'вакансія неактивна'). Run by cron once a day."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Check at most this many vacancies.")

    def handle(self, *args, **options):
        with job_lock(LOCK, LOCK_TTL) as acquired:
            if not acquired:
                self.stdout.write("check_active is already running, skipping.")
                return
            run = check_active(limit=options["limit"])
            pruned = prune_housekeeping()

        self.stdout.write(
            f"Checked {run.checked}: {run.closed} closed, {run.still_active} still active, "
            f"{run.unknown} unknown. Pruned {pruned} old bot update ids."
        )
        if run.checked and run.unknown == run.checked:
            raise CommandError("Could not check any vacancy (is DOU reachable?).")


def prune_housekeeping() -> int:
    """Daily cleanup that belongs to no single feature: old Telegram update ids."""
    from bot.models import ProcessedUpdate

    return ProcessedUpdate.prune()
