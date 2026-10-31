from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from radar.locks import FETCH_LOCK, job_lock
from radar.models import Source
from radar.services.fetcher import fetch_all_sources

# Longer than a normal run (a few sources x a few seconds), short enough that a
# crashed run doesn't block the next cron tick for long.
LOCK_TTL = timedelta(minutes=10)


class Command(BaseCommand):
    help = "Fetch enabled RSS sources and save new/updated vacancies. Run by cron every 30 min."

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            action="append",
            help="Only fetch sources of this category (can be repeated).",
        )

    def handle(self, *args, **options):
        sources = Source.objects.filter(is_enabled=True)
        if options["category"]:
            sources = sources.filter(category__in=options["category"])
        if not sources.exists():
            self.stdout.write("No enabled sources to fetch.")
            return

        with job_lock(FETCH_LOCK, LOCK_TTL) as acquired:
            if not acquired:
                self.stdout.write("Another fetch is already running, skipping.")
                return
            results = fetch_all_sources(sources)

        for r in results:
            status = "ok" if r.ok else f"FAILED ({r.error})"
            self.stdout.write(f"{r.source.name}: {status}, {r.created} new, {r.updated} updated")

        created = sum(r.created for r in results)
        failed = [r for r in results if not r.ok]
        self.stdout.write(f"Total: {created} new vacancies, {len(failed)} failed sources.")
        # Fail the job only if everything failed, so one flaky feed doesn't
        # turn the whole scheduled workflow red.
        if failed and len(failed) == len(results):
            raise CommandError("All sources failed.")
