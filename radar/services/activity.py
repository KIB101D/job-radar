"""
Which vacancies are closed?

The RSS feed only lists the latest items, so a vacancy dropping out of the feed
does NOT mean it was closed: it may just have been pushed out by newer ones.
Instead, check_active opens the vacancy page itself. On DOU a closed vacancy is:
- HTTP 404/410, or
- HTTP 200 with "(вакансія неактивна)" in the page's <h1> (the description stays).

Only vacancies that left the feed are checked (a vacancy still in the feed is
active by definition), each at most once per CHECK_INTERVAL, one request at a
time with a pause in between.
"""

import enum
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from bs4 import BeautifulSoup
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from radar.models import Vacancy

from .fetcher import FetchError, build_client, fetch_url

logger = logging.getLogger(__name__)

# Seen in the feed this recently -> certainly active, no need to open the page.
FEED_GRACE = timedelta(hours=24)
# Don't re-check the same vacancy more often than this.
CHECK_INTERVAL = timedelta(hours=20)

INACTIVE_MARKERS = ("вакансія неактивна", "вакансия неактивна")
GONE_STATUSES = frozenset({404, 410})


class Status(enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"  # network trouble, 403, 5xx...: don't change anything


@dataclass
class ActivityRun:
    checked: int = 0
    closed: int = 0
    still_active: int = 0
    unknown: int = 0
    errors: list[str] = field(default_factory=list)


def page_says_inactive(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    text = (heading.get_text(" ", strip=True) if heading else "").lower()
    return any(marker in text for marker in INACTIVE_MARKERS)


def check_vacancy(client: httpx.Client, url: str, *, sleep=time.sleep) -> Status:
    try:
        response = fetch_url(client, url, sleep=sleep)
    except FetchError as exc:
        if exc.status_code in GONE_STATUSES:
            return Status.INACTIVE
        logger.warning("Could not check %s: %s", url, exc)
        return Status.UNKNOWN
    return Status.INACTIVE if page_says_inactive(response.text) else Status.ACTIVE


def due_for_check(now=None):
    now = now or timezone.now()
    return (
        Vacancy.objects.active()
        .filter(last_seen_at__lt=now - FEED_GRACE)
        .filter(Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=now - CHECK_INTERVAL))
        .order_by(F("last_checked_at").asc(nulls_first=True), "last_seen_at")
    )


def check_active(*, limit: int | None = None, client=None, sleep=time.sleep) -> ActivityRun:
    """Check up to `limit` vacancies, oldest-checked first, one at a time."""
    limit = limit or settings.CHECK_ACTIVE_MAX_PER_RUN
    run = ActivityRun()
    own_client = client is None
    client = client or build_client()
    try:
        for index, vacancy in enumerate(due_for_check()[:limit]):
            if index and settings.CHECK_ACTIVE_DELAY_SECONDS > 0:
                sleep(settings.CHECK_ACTIVE_DELAY_SECONDS)
            status = check_vacancy(client, vacancy.url, sleep=sleep)
            run.checked += 1
            fields = {"last_checked_at": timezone.now()}
            if status is Status.INACTIVE:
                fields["is_active"] = False
                run.closed += 1
            elif status is Status.ACTIVE:
                run.still_active += 1
            else:
                run.unknown += 1
            Vacancy.objects.filter(pk=vacancy.pk).update(**fields)
    finally:
        if own_client:
            client.close()
    return run
