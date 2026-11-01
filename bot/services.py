"""
"Перевірити зараз": a rate-limited, on-demand check.

It normally just reads our DB. Only if the vacancies are stale (last good fetch
over 10 minutes ago) does it fetch DOU itself, and then under the same DB lock
as the cron job, so several presses (or a press during a cron run) never start
parallel fetches. This is the one place scraping may run during a request.
"""

import math
from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from radar.locks import FETCH_LOCK, FETCH_LOCK_TTL, job_lock
from radar.models import Source
from radar.services.fetcher import fetch_all_sources
from subscriptions.delivery import mark_delivered, undelivered_new
from subscriptions.models import Subscription

CHECK_COOLDOWN = timedelta(seconds=60)
STALE_AFTER = timedelta(minutes=10)


@dataclass
class CheckResult:
    wait_seconds: int = 0
    fetched: bool = False
    vacancies: list = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.wait_seconds == 0


def claim_check(subscription: Subscription, now=None) -> int:
    """
    Take the per-chat check slot. Returns 0 if allowed, otherwise the seconds left.
    One conditional UPDATE, so two fast taps can't both pass.
    """
    now = now or timezone.now()
    allowed = (
        Subscription.objects.filter(pk=subscription.pk)
        .filter(Q(last_check_at__isnull=True) | Q(last_check_at__lte=now - CHECK_COOLDOWN))
        .update(last_check_at=now)
    )
    if allowed:
        subscription.last_check_at = now
        return 0
    subscription.refresh_from_db(fields=["last_check_at"])
    left = (subscription.last_check_at + CHECK_COOLDOWN - now).total_seconds()
    return max(math.ceil(left), 1)


def sources_for(subscription: Subscription):
    sources = Source.objects.filter(is_enabled=True)
    if subscription.categories:
        sources = sources.filter(category__in=subscription.categories)
    return sources


def is_stale(subscription: Subscription, now=None) -> bool:
    now = now or timezone.now()
    last = sources_for(subscription).aggregate(m=Max("last_fetched_at"))["m"]
    return last is None or last < now - STALE_AFTER


def refresh_if_stale(subscription: Subscription) -> bool:
    """Fetch the subscription's sources if stale and nobody else is fetching."""
    if not is_stale(subscription):
        return False
    with job_lock(FETCH_LOCK, FETCH_LOCK_TTL) as acquired:
        if not acquired:
            return False  # a fetch is already running; its results land in the DB
        fetch_all_sources(sources_for(subscription))
        return True


def check_now(subscription: Subscription, *, on_fetch=None) -> CheckResult:
    """
    Rate limit, refresh if stale, then return (and mark as delivered) new matches
    this chat hasn't seen. `on_fetch` is called right before a slow fetch, so the
    bot can say "Перевіряю DOU…" first.
    """
    wait = claim_check(subscription)
    if wait:
        return CheckResult(wait_seconds=wait)

    fetched = False
    if is_stale(subscription):
        if on_fetch:
            on_fetch()
        fetched = refresh_if_stale(subscription)

    vacancies = undelivered_new(subscription)
    mark_delivered(subscription, vacancies)
    return CheckResult(fetched=fetched, vacancies=vacancies)
