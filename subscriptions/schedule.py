"""
When a subscription's digests go out. Pure functions, no DB.

Slots are aligned to the wall clock in the user's own time zone and anchored at
09:00, so they read naturally ("next digest at 18:00"):

    hourly     every hour
    every_3h   09, 12, 15, 18, 21, 00, 03, 06
    every_6h   09, 15, 21, 03
    every_12h  09, 21
    daily      09
    off        never

Quiet hours: a slot that falls inside the quiet window is moved to the moment
the window ends, so whatever arrived at night comes in one morning message
instead of being lost (or arriving at 03:00).
"""

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.utils import timezone

from .models import Frequency

ANCHOR_HOUR = 9
STEP_HOURS = {
    Frequency.HOURLY: 1,
    Frequency.EVERY_3H: 3,
    Frequency.EVERY_6H: 6,
    Frequency.EVERY_12H: 12,
    Frequency.DAILY: 24,
}
DEFAULT_TIMEZONE = "Europe/Kyiv"
_SEARCH_DAYS = 3  # every frequency has a slot at least once a day

WEEKDAYS_UK = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def is_valid_timezone(name: str) -> bool:
    return bool(name) and name in available_timezones()


def user_tz(subscription) -> ZoneInfo:
    try:
        return ZoneInfo(subscription.timezone or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def cadence_hours(frequency: str) -> list[int]:
    """Clock hours of the raw cadence, before quiet hours are applied."""
    step = STEP_HOURS.get(frequency)
    if step is None:
        return []
    return sorted(h for h in range(24) if (h - ANCHOR_HOUR) % step == 0)


def is_quiet_hour(hour: int, start: int, end: int) -> bool:
    """Quiet window [start, end), may wrap midnight: 22..8 -> 22, 23, 0, ..., 7."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def delivery_hours(subscription) -> list[int]:
    """Hours (local) at which digests actually go out, quiet hours applied."""
    hours = set()
    for hour in cadence_hours(subscription.frequency):
        if subscription.quiet_hours_enabled and is_quiet_hour(
            hour, subscription.quiet_hours_start, subscription.quiet_hours_end
        ):
            hour = subscription.quiet_hours_end  # deliver when the quiet window ends
        hours.add(hour)
    return sorted(hours)


def _slots(subscription, start: date, days: int) -> Iterator[datetime]:
    """Aware datetimes (user tz) of every slot from `start` for `days` days, ascending."""
    tz = user_tz(subscription)
    hours = delivery_hours(subscription)
    for offset in range(days):
        day = start + timedelta(days=offset)
        for hour in hours:
            yield datetime.combine(day, time(hour), tzinfo=tz)


def next_slot(subscription, now: datetime | None = None) -> datetime | None:
    """The first slot strictly after `now`, in the user's time zone."""
    now = now or timezone.now()
    local_today = now.astimezone(user_tz(subscription)).date()
    for slot in _slots(subscription, local_today, _SEARCH_DAYS):
        if slot > now:
            return slot
    return None


def previous_slot(subscription, now: datetime | None = None) -> datetime | None:
    """The latest slot at or before `now` (a digest is due if none was sent since)."""
    now = now or timezone.now()
    local_today = now.astimezone(user_tz(subscription)).date()
    latest = None
    for slot in _slots(subscription, local_today - timedelta(days=2), _SEARCH_DAYS):
        if slot <= now:
            latest = slot
        else:
            break
    return latest


def is_due(subscription, now: datetime | None = None) -> bool:
    """A digest is due if a slot has passed since the last digest went out."""
    if subscription.paused or subscription.frequency == Frequency.OFF:
        return False
    slot = previous_slot(subscription, now)
    if slot is None:
        return False
    last = subscription.last_digest_at
    return last is None or last < slot


def next_slot_label(subscription, now: datetime | None = None) -> str:
    """'о 18:00', 'завтра о 09:00', 'ср о 09:00', or '' when notifications are off."""
    now = now or timezone.now()
    slot = next_slot(subscription, now)
    if slot is None:
        return ""
    today = now.astimezone(slot.tzinfo).date()
    clock = slot.strftime("%H:%M")
    if slot.date() == today:
        return f"о {clock}"
    if slot.date() == today + timedelta(days=1):
        return f"завтра о {clock}"
    return f"{WEEKDAYS_UK[slot.weekday()]} о {clock}"


def timezone_label(subscription) -> str:
    """'Europe/Madrid' -> 'Madrid' for compact UI."""
    name = str(user_tz(subscription))
    return name.rsplit("/", 1)[-1].replace("_", " ")
