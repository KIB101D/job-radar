from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from subscriptions.models import Frequency
from subscriptions.schedule import (
    cadence_hours,
    delivery_hours,
    is_due,
    is_quiet_hour,
    is_valid_timezone,
    next_slot,
    next_slot_label,
    previous_slot,
    timezone_label,
)

KYIV = ZoneInfo("Europe/Kyiv")
MADRID = ZoneInfo("Europe/Madrid")


def sub(**overrides):
    defaults = {
        "frequency": Frequency.EVERY_3H,
        "timezone": "Europe/Kyiv",
        "quiet_hours_enabled": True,
        "quiet_hours_start": 22,
        "quiet_hours_end": 8,
        "paused": False,
        "last_digest_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def at(tz, *args):
    return datetime(*args, tzinfo=tz)


# --- cadence & quiet hours -------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "hours"),
    [
        (Frequency.HOURLY, list(range(24))),
        (Frequency.EVERY_3H, [0, 3, 6, 9, 12, 15, 18, 21]),
        (Frequency.EVERY_6H, [3, 9, 15, 21]),
        (Frequency.EVERY_12H, [9, 21]),
        (Frequency.DAILY, [9]),
        (Frequency.OFF, []),
    ],
)
def test_cadence_is_anchored_at_nine(frequency, hours):
    assert cadence_hours(frequency) == hours


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(21, False), (22, True), (23, True), (0, True), (7, True), (8, False), (12, False)],
)
def test_quiet_window_wraps_midnight(hour, expected):
    assert is_quiet_hour(hour, 22, 8) is expected


def test_quiet_window_same_day_and_disabled():
    assert is_quiet_hour(14, 13, 15) and not is_quiet_hour(15, 13, 15)
    assert not is_quiet_hour(3, 8, 8)  # start == end: no quiet hours


def test_every_3h_with_default_quiet_hours():
    # 00, 03, 06 are quiet -> moved to 08:00; the rest stay.
    assert delivery_hours(sub()) == [8, 9, 12, 15, 18, 21]


def test_hourly_night_collapses_into_morning():
    assert delivery_hours(sub(frequency=Frequency.HOURLY)) == list(range(8, 22))


def test_quiet_hours_off_keeps_night_slots():
    assert delivery_hours(sub(quiet_hours_enabled=False)) == [0, 3, 6, 9, 12, 15, 18, 21]


def test_daily_inside_quiet_window_moves_to_window_end():
    s = sub(frequency=Frequency.DAILY, quiet_hours_start=22, quiet_hours_end=10)
    assert delivery_hours(s) == [10]


# --- next / previous slot in the user's time zone -------------------------------------


def test_next_slot_every_3h_afternoon():
    now = at(KYIV, 2026, 11, 2, 16, 40)
    assert next_slot(sub(), now) == at(KYIV, 2026, 11, 2, 18, 0)


def test_next_slot_is_strictly_after_now():
    now = at(KYIV, 2026, 11, 2, 18, 0)
    assert next_slot(sub(), now) == at(KYIV, 2026, 11, 2, 21, 0)


def test_next_slot_late_evening_goes_to_morning():
    now = at(KYIV, 2026, 11, 2, 21, 30)
    assert next_slot(sub(), now) == at(KYIV, 2026, 11, 3, 8, 0)


def test_daily_next_slot_is_nine_tomorrow():
    now = at(KYIV, 2026, 11, 2, 10, 0)
    assert next_slot(sub(frequency=Frequency.DAILY), now) == at(KYIV, 2026, 11, 3, 9, 0)


def test_slots_follow_users_own_time_zone():
    """09:00 in Madrid is 10:00 in Kyiv: the same UTC moment gives different answers."""
    now = datetime(2026, 11, 2, 7, 30, tzinfo=UTC)  # 09:30 Kyiv, 08:30 Madrid
    madrid = sub(frequency=Frequency.DAILY, timezone="Europe/Madrid")
    kyiv = sub(frequency=Frequency.DAILY)

    assert next_slot(madrid, now) == at(MADRID, 2026, 11, 2, 9, 0)
    assert next_slot(kyiv, now) == at(KYIV, 2026, 11, 3, 9, 0)


def test_off_has_no_slots():
    now = at(KYIV, 2026, 11, 2, 12, 0)
    s = sub(frequency=Frequency.OFF)
    assert next_slot(s, now) is None and previous_slot(s, now) is None


def test_previous_slot():
    now = at(KYIV, 2026, 11, 2, 16, 40)
    assert previous_slot(sub(), now) == at(KYIV, 2026, 11, 2, 15, 0)
    early = at(KYIV, 2026, 11, 2, 7, 0)  # before the 08:00 morning slot
    assert previous_slot(sub(), early) == at(KYIV, 2026, 11, 1, 21, 0)


def test_dst_change_keeps_wall_clock_slots():
    # Kyiv switches from UTC+3 to UTC+2 on 2026-10-25.
    s = sub(frequency=Frequency.DAILY)
    before = next_slot(s, at(KYIV, 2026, 10, 23, 12, 0))  # -> 24.10 09:00, UTC+3
    after = next_slot(s, at(KYIV, 2026, 10, 25, 12, 0))  # -> 26.10 09:00, UTC+2
    assert before.astimezone(UTC).hour == 6
    assert after.astimezone(UTC).hour == 7
    assert before.astimezone(KYIV).hour == after.astimezone(KYIV).hour == 9


# --- due -------------------------------------------------------------------------


def test_is_due_when_slot_passed_since_last_digest():
    now = at(KYIV, 2026, 11, 2, 15, 5)
    assert is_due(sub(last_digest_at=at(KYIV, 2026, 11, 2, 12, 2)), now)
    assert not is_due(sub(last_digest_at=at(KYIV, 2026, 11, 2, 15, 1)), now)
    assert is_due(sub(last_digest_at=None), now)


def test_paused_or_off_is_never_due():
    now = at(KYIV, 2026, 11, 2, 15, 5)
    assert not is_due(sub(paused=True), now)
    assert not is_due(sub(frequency=Frequency.OFF), now)


# --- labels ----------------------------------------------------------------------


def test_next_slot_labels():
    s = sub()
    assert next_slot_label(s, at(KYIV, 2026, 11, 2, 16, 40)) == "о 18:00"
    assert next_slot_label(s, at(KYIV, 2026, 11, 2, 21, 30)) == "завтра о 08:00"
    assert next_slot_label(sub(frequency=Frequency.OFF), at(KYIV, 2026, 11, 2, 9, 0)) == ""


def test_timezone_helpers():
    assert timezone_label(sub(timezone="America/New_York")) == "New York"
    assert timezone_label(sub(timezone="Not/AZone")) == "Kyiv"  # safe fallback
    assert is_valid_timezone("Europe/Madrid")
    assert not is_valid_timezone("Mars/Olympus")
