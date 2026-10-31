"""Small Ukrainian-language helpers for templates."""

from datetime import datetime

from django import template
from django.utils import timezone

from radar.services.parser import REMOTE_LOCATION

register = template.Library()


def uk_plural_form(n: int, one: str, few: str, many: str) -> str:
    """1 вакансія, 2 вакансії, 5 вакансій, 11 вакансій, 21 вакансія."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@register.filter
def uk_plural(n, forms: str) -> str:
    """{{ count|uk_plural:"нова вакансія,нові вакансії,нових вакансій" }}"""
    one, few, many = forms.split(",")
    return uk_plural_form(n, one, few, many)


@register.filter
def ago(value: datetime, now: datetime | None = None) -> str:
    """'щойно', '5 хв тому', '3 год тому', 'вчора', '4 дні тому'."""
    if not value:
        return ""
    now = now or timezone.now()
    seconds = max((now - value).total_seconds(), 0)
    minutes = int(seconds // 60)
    hours = int(seconds // 3600)
    days = int(seconds // 86400)
    if minutes < 1:
        return "щойно"
    if hours < 1:
        return f"{minutes} хв тому"
    if days < 1:
        return f"{hours} год тому"
    if days == 1:
        return "вчора"
    return f"{days} {uk_plural_form(days, 'день', 'дні', 'днів')} тому"


@register.filter
def places(location: str) -> str:
    """Location without 'віддалено' (shown as a separate tag)."""
    parts = [p.strip() for p in (location or "").split(",")]
    return ", ".join(p for p in parts if p and p.lower() != REMOTE_LOCATION)


@register.filter
def is_fresh(value: datetime, hours: int = 24) -> bool:
    """True if the moment is within the last `hours` hours (marks new vacancies)."""
    return bool(value) and (timezone.now() - value).total_seconds() < int(hours) * 3600
