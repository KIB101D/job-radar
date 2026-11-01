"""
Scheduled digests: one Telegram message per subscription with the matches it
hasn't seen, sent when its clock-aligned slot comes (subscriptions/schedule.py).

Run by `send_digests` every 15 minutes. Safe to run late, twice or in parallel:
- a slot is claimed with one conditional UPDATE on last_digest_at, so two runs
  can't both send the same digest;
- if sending fails, the claim is rolled back and the next run tries again;
- vacancies are recorded as delivered only after Telegram accepted the message.
"""

import logging
import time
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from subscriptions import schedule
from subscriptions.delivery import mark_delivered, undelivered_new
from subscriptions.models import Frequency, Subscription

from . import texts
from .formatting import radar_url, vacancy_list
from .telegram import TelegramError, get_client

logger = logging.getLogger(__name__)

# Telegram allows ~30 messages/second overall; stay well below it.
SEND_INTERVAL_SECONDS = 0.05
MAX_RETRY_AFTER_SECONDS = 30


@dataclass
class DigestRun:
    checked: int = 0
    due: int = 0
    sent: int = 0
    empty: int = 0
    failed: int = 0
    unlinked: int = 0
    errors: list[str] = field(default_factory=list)


def candidates():
    """Linked chats that want automatic digests at all."""
    return (
        Subscription.objects.filter(chat_id__isnull=False, paused=False)
        .exclude(frequency=Frequency.OFF)
        .order_by("pk")
    )


def claim_slot(subscription: Subscription, now) -> bool:
    """
    Mark the current slot as handled, atomically. False if it isn't due or another
    run got there first.
    """
    slot = schedule.previous_slot(subscription, now)
    if slot is None:
        return False
    claimed = (
        Subscription.objects.filter(pk=subscription.pk)
        .filter(Q(last_digest_at__isnull=True) | Q(last_digest_at__lt=slot))
        .update(last_digest_at=now)
    )
    return claimed == 1


def release_slot(subscription: Subscription, previous) -> None:
    """Undo a claim after a failed send, so the next run retries this slot."""
    Subscription.objects.filter(pk=subscription.pk).update(last_digest_at=previous)


def digest_text(subscription: Subscription, vacancies, now) -> str:
    header = texts.DIGEST_HEADER.format(count_text=texts.new_vacancies(len(vacancies)))
    text, _ = vacancy_list(header, vacancies, more_url=radar_url(subscription))
    when = schedule.next_slot_label(subscription, now)
    if when:
        text += "\n\n" + texts.DIGEST_NEXT.format(when=when)
    return text


def send_with_retry(chat_id: int, text: str, *, sleep=time.sleep) -> None:
    """One retry on 429 Too Many Requests, after the pause Telegram asks for."""
    client = get_client()
    try:
        client.send_message(chat_id, text)
    except TelegramError as exc:
        if exc.error_code != 429:
            raise
        sleep(min(exc.retry_after or 1, MAX_RETRY_AFTER_SECONDS))
        client.send_message(chat_id, text)


def send_digest(subscription: Subscription, now, run: DigestRun, *, sleep=time.sleep) -> None:
    if not schedule.is_due(subscription, now):
        return
    run.due += 1
    previous = subscription.last_digest_at
    if not claim_slot(subscription, now):
        return  # another run is handling it

    vacancies = undelivered_new(subscription)
    if not vacancies:
        run.empty += 1  # nothing new: no message, slot still used up
        return

    try:
        send_with_retry(
            subscription.chat_id, digest_text(subscription, vacancies, now), sleep=sleep
        )
    except TelegramError as exc:
        if exc.chat_unavailable:
            # Blocked the bot or deleted the chat: stop writing to it.
            Subscription.objects.filter(pk=subscription.pk).update(chat_id=None, linked_at=None)
            run.unlinked += 1
            return
        release_slot(subscription, previous)
        run.failed += 1
        run.errors.append(f"subscription {subscription.pk}: {exc}")
        logger.warning("Digest for subscription %s failed: %s", subscription.pk, exc)
        return

    # The message covers all of them ("…і ще N на сайті" links the rest).
    mark_delivered(subscription, vacancies)
    run.sent += 1
    sleep(SEND_INTERVAL_SECONDS)


def send_due_digests(now=None, *, sleep=time.sleep) -> DigestRun:
    now = now or timezone.now()
    run = DigestRun()
    for subscription in candidates().iterator():
        run.checked += 1
        try:
            send_digest(subscription, now, run, sleep=sleep)
        except Exception as exc:  # one bad subscription must not stop the rest
            run.failed += 1
            run.errors.append(f"subscription {subscription.pk}: {exc!r}")
            logger.exception("Digest for subscription %s crashed", subscription.pk)
    return run
