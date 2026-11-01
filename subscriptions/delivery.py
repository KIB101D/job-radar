"""
What a Telegram chat gets, shared by the bot (Phase 3) and digests (Phase 4).

A Delivery row means "this vacancy was sent to this subscription". Everything
that sends vacancies records them here, so nothing is ever sent twice and the
site can mark them as seen.
"""

from django.db.models import Q

from radar.matching import Filters, vacancy_matches
from radar.search import Page, base_queryset, search

from .models import Delivery, Subscription


def filters_for(subscription: Subscription) -> Filters:
    return Filters.from_subscription(subscription)


def newest_matches(subscription: Subscription, *, offset: int = 0, limit: int = 5) -> Page:
    """All current matches, newest first (first batch, "Показати ще", "Останні 10")."""
    return search(filters_for(subscription), offset=offset, limit=limit)


def undelivered_new(subscription: Subscription) -> list:
    """
    Matches that appeared after the chat was linked and were never sent to it,
    newest first. The linked_at baseline keeps the first digest from being a
    dump of everything that was already in the database.
    """
    filters = filters_for(subscription)
    qs = base_queryset(filters).exclude(deliveries__subscription=subscription)
    if subscription.linked_at:
        qs = qs.filter(first_seen_at__gte=subscription.linked_at)
    return [v for v in qs.newest_first() if vacancy_matches(filters, v)]


def mark_delivered(subscription: Subscription, vacancies) -> int:
    rows = [Delivery(subscription=subscription, vacancy=v) for v in vacancies]
    Delivery.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def linked_subscription(chat_id: int) -> Subscription | None:
    return Subscription.objects.filter(chat_id=chat_id).first()


def link_chat(subscription: Subscription, chat_id: int) -> bool:
    """
    Attach a Telegram chat to a subscription. One chat follows one radar, so the
    chat is detached from any other subscription first. Returns True if the chat
    was newly linked (False when /start is pressed again in the same chat).
    """
    from django.db import transaction
    from django.utils import timezone

    with transaction.atomic():
        Subscription.objects.filter(Q(chat_id=chat_id) & ~Q(pk=subscription.pk)).update(
            chat_id=None, linked_at=None
        )
        if subscription.chat_id == chat_id:
            return False
        subscription.chat_id = chat_id
        subscription.linked_at = timezone.now()
        subscription.save(update_fields=["chat_id", "linked_at", "updated_at"])
        return True


def unlink_chat(chat_id: int) -> Subscription | None:
    subscription = linked_subscription(chat_id)
    if subscription:
        subscription.chat_id = None
        subscription.linked_at = None
        subscription.save(update_fields=["chat_id", "linked_at", "updated_at"])
    return subscription
