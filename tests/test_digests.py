"""
send_digests: frequency, quiet hours and time zones, end to end.

Times are written in the user's local zone. Kyiv is UTC+2 and Madrid UTC+1 in
November 2026.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.core.management import CommandError, call_command

from bot.digests import claim_slot, send_due_digests
from radar.locks import acquire
from subscriptions.models import Delivery, Frequency, Subscription

pytestmark = pytest.mark.django_db

KYIV = ZoneInfo("Europe/Kyiv")
MADRID = ZoneInfo("Europe/Madrid")


def kyiv(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=KYIV)


def madrid(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=MADRID)


def make_sub(chat_id=100, **kwargs):
    defaults = {
        "include_keywords": ["python"],
        "chat_id": chat_id,
        "linked_at": kyiv(2, 8),
        "frequency": Frequency.EVERY_3H,
    }
    defaults.update(kwargs)
    return Subscription.objects.create(**defaults)


def run_at(moment):
    with time_machine.travel(moment, tick=False):
        return send_due_digests(sleep=lambda s: None)


@pytest.fixture
def vacancy(make_vacancy):
    def _make(title="Python dev", at=None, **kwargs):
        return make_vacancy(title=title, first_seen_at=at or kyiv(2, 13), **kwargs)

    return _make


# --- the basic slot ------------------------------------------------------------------------


def test_digest_is_sent_at_the_slot_with_only_new_matches(tg, vacancy):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy("Python dev A")
    vacancy("Python dev B")
    vacancy("Java dev")  # doesn't match
    vacancy("Python before linking", at=kyiv(2, 7))  # before the chat was linked

    run = run_at(kyiv(2, 15, 5))

    assert (run.due, run.sent) == (1, 1)
    message = tg.sent()[0]
    assert message["chat_id"] == 100
    text = message["text"]
    assert "2 нові вакансії" in text and "Python dev A" in text and "Python dev B" in text
    assert "Java" not in text and "before linking" not in text
    assert text.endswith("⏰ Наступний дайджест о 18:00.")
    assert Delivery.objects.filter(subscription=sub).count() == 2
    sub.refresh_from_db()
    assert sub.last_digest_at == kyiv(2, 15, 5)


def test_same_slot_is_never_sent_twice(tg, vacancy):
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    run_at(kyiv(2, 15, 5))
    vacancy("Python dev 2", at=kyiv(2, 15, 10))

    run = run_at(kyiv(2, 15, 20))  # next cron tick, same slot

    assert run.sent == 0 and len(tg.sent()) == 1


def test_nothing_between_slots(tg, vacancy):
    make_sub(last_digest_at=kyiv(2, 15, 5))
    vacancy()
    assert run_at(kyiv(2, 16, 40)).due == 0
    assert tg.sent() == []


def test_nothing_new_means_no_message_but_slot_is_used(tg, vacancy):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))

    run = run_at(kyiv(2, 15, 5))

    assert (run.due, run.sent, run.empty) == (1, 0, 1)
    assert tg.sent() == []
    sub.refresh_from_db()
    assert sub.last_digest_at == kyiv(2, 15, 5)

    vacancy(at=kyiv(2, 15, 10))
    assert run_at(kyiv(2, 15, 20)).sent == 0  # waits for the 18:00 slot
    assert run_at(kyiv(2, 18, 5)).sent == 1


def test_late_cron_still_delivers_the_slot(tg, vacancy):
    """GitHub cron can be 10-30 min late: 15:00 slot, run at 15:40."""
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    assert run_at(kyiv(2, 15, 40)).sent == 1


def test_first_digest_after_linking(tg, vacancy):
    make_sub(last_digest_at=None, linked_at=kyiv(2, 13, 30))
    vacancy(at=kyiv(2, 14))
    assert run_at(kyiv(2, 15, 5)).sent == 1


# --- quiet hours ------------------------------------------------------------------------------


def test_quiet_hours_hold_night_vacancies_for_one_morning_message(tg, vacancy):
    make_sub(frequency=Frequency.HOURLY, last_digest_at=kyiv(2, 21, 3))
    vacancy("Python night 1", at=kyiv(2, 23, 30))
    vacancy("Python night 2", at=kyiv(3, 3))

    for moment in (kyiv(2, 23, 50), kyiv(3, 0, 5), kyiv(3, 3, 10), kyiv(3, 7, 50)):
        assert run_at(moment).sent == 0, moment
    assert tg.sent() == []

    assert run_at(kyiv(3, 8, 5)).sent == 1
    text = tg.last_text()
    assert "2 нові вакансії" in text and "night 1" in text and "night 2" in text


def test_quiet_hours_off_sends_at_night(tg, vacancy):
    make_sub(
        frequency=Frequency.HOURLY,
        quiet_hours_enabled=False,
        last_digest_at=kyiv(2, 23, 3),
    )
    vacancy(at=kyiv(2, 23, 30))
    assert run_at(kyiv(3, 0, 5)).sent == 1


# --- time zones ------------------------------------------------------------------------------


def test_daily_digest_follows_each_users_own_clock(tg, vacancy):
    kyiv_sub = make_sub(chat_id=1, frequency=Frequency.DAILY, last_digest_at=kyiv(1, 9, 1))
    madrid_sub = make_sub(
        chat_id=2,
        frequency=Frequency.DAILY,
        timezone="Europe/Madrid",
        last_digest_at=madrid(1, 9, 1),
    )
    vacancy(at=kyiv(2, 8, 30))  # after both chats were linked (08:00 Kyiv = 07:00 Madrid)

    # 07:30 UTC = 09:30 Kyiv (slot passed) = 08:30 Madrid (not yet).
    run_at(datetime(2026, 11, 2, 7, 30, tzinfo=ZoneInfo("UTC")))
    assert [m["chat_id"] for m in tg.sent()] == [kyiv_sub.chat_id]

    # 08:05 UTC = 09:05 Madrid.
    run_at(datetime(2026, 11, 2, 8, 5, tzinfo=ZoneInfo("UTC")))
    assert [m["chat_id"] for m in tg.sent()] == [kyiv_sub.chat_id, madrid_sub.chat_id]
    assert "завтра о 09:00" in tg.last_text()


# --- who gets nothing --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [{"paused": True}, {"frequency": Frequency.OFF}, {"chat_id": None}],
    ids=["paused", "off", "not-linked"],
)
def test_skipped_subscriptions(tg, vacancy, overrides):
    make_sub(last_digest_at=kyiv(2, 12, 2), **overrides)
    vacancy()
    assert run_at(kyiv(2, 15, 5)).sent == 0
    assert tg.sent() == []


def test_already_delivered_by_check_now_is_not_repeated(tg, vacancy):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    seen = vacancy("Python seen via check now")
    vacancy("Python fresh")
    Delivery.objects.create(subscription=sub, vacancy=seen)

    run_at(kyiv(2, 15, 5))

    assert "1 нова вакансія" in tg.last_text() and "check now" not in tg.last_text()


def test_closed_vacancies_are_not_sent(tg, vacancy):
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy("Python closed", is_active=False)
    assert run_at(kyiv(2, 15, 5)).sent == 0


def test_long_digest_is_capped_and_links_to_site(tg, vacancy, settings):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    for i in range(20):
        vacancy(f"Python role {i}")

    run_at(kyiv(2, 15, 5))

    text = tg.last_text()
    assert text.count("<a href=") == 15
    assert f"…і ще 5 на сайті: {settings.SITE_URL}/s/{sub.token}/" in text
    assert len(text) < 4096
    assert Delivery.objects.filter(subscription=sub).count() == 20  # the message covers all


# --- failures --------------------------------------------------------------------------------


def test_failed_send_is_retried_next_run(tg, vacancy):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    tg.fail_next("sendMessage", 502, "Bad Gateway")

    run = run_at(kyiv(2, 15, 5))

    assert (run.sent, run.failed) == (0, 1)
    sub.refresh_from_db()
    assert sub.last_digest_at == kyiv(2, 12, 2)  # claim released
    assert Delivery.objects.count() == 0

    assert run_at(kyiv(2, 15, 20)).sent == 1


def test_blocked_bot_unlinks_chat(tg, vacancy):
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    tg.fail_next("sendMessage", 403, "Forbidden: bot was blocked by the user")

    run = run_at(kyiv(2, 15, 5))

    sub.refresh_from_db()
    assert run.unlinked == 1 and sub.chat_id is None


def test_rate_limit_is_respected_and_retried(tg, vacancy):
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    tg.fail_next("sendMessage", 429, "Too Many Requests: retry after 3", {"retry_after": 3})
    sleeps = []

    with time_machine.travel(kyiv(2, 15, 5), tick=False):
        run = send_due_digests(sleep=sleeps.append)

    assert run.sent == 1 and 3 in sleeps


def test_one_crashing_subscription_does_not_stop_others(tg, vacancy, monkeypatch):
    from bot import digests

    make_sub(chat_id=1, last_digest_at=kyiv(2, 12, 2))
    make_sub(chat_id=2, last_digest_at=kyiv(2, 12, 2))
    vacancy()
    real = digests.digest_text
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(digests, "digest_text", flaky)
    run = run_at(kyiv(2, 15, 5))
    assert (run.failed, run.sent) == (1, 1)


def test_parallel_runs_cannot_both_claim_a_slot():
    sub = make_sub(last_digest_at=kyiv(2, 12, 2))
    now = kyiv(2, 15, 5)
    assert claim_slot(sub, now) is True
    assert claim_slot(sub, now) is False


# --- command -----------------------------------------------------------------------------------


def test_command_reports(tg, vacancy, capsys):
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    with time_machine.travel(kyiv(2, 15, 5), tick=False):
        call_command("send_digests")
    assert "1 due, 1 sent" in capsys.readouterr().out


def test_command_skips_while_another_run_holds_the_lock(tg, capsys):
    acquire("send_digests", timedelta(minutes=10))
    call_command("send_digests")
    assert "in progress" in capsys.readouterr().out


def test_command_fails_when_every_due_digest_failed(tg, vacancy):
    make_sub(last_digest_at=kyiv(2, 12, 2))
    vacancy()
    tg.fail_next("sendMessage", 502, "Bad Gateway")
    with time_machine.travel(kyiv(2, 15, 5), tick=False), pytest.raises(CommandError):
        call_command("send_digests")


def test_command_requires_token(settings):
    settings.TELEGRAM_BOT_TOKEN = ""
    with pytest.raises(CommandError, match="TELEGRAM_BOT_TOKEN"):
        call_command("send_digests")


# --- the first slot after linking, and what the last digest did ----------------------


def test_slot_that_passed_before_linking_does_not_count(tg, make_vacancy):
    """Daily radar linked at 17:28: the 09:00 slot was before that, the next is tomorrow."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    import time_machine

    from bot.digests import send_due_digests
    from subscriptions.models import Frequency, Subscription

    madrid = ZoneInfo("Europe/Madrid")
    linked = dt.datetime(2026, 11, 2, 17, 28, tzinfo=madrid)
    sub = Subscription.objects.create(
        categories=["Python"],
        chat_id=4242,
        linked_at=linked,
        frequency=Frequency.DAILY,
        timezone="Europe/Madrid",
    )
    make_vacancy(title="Python new", first_seen_at=linked + dt.timedelta(minutes=10))

    with time_machine.travel(dt.datetime(2026, 11, 2, 18, 2, tzinfo=madrid), tick=False):
        assert send_due_digests().due == 0
    with time_machine.travel(dt.datetime(2026, 11, 3, 9, 1, tzinfo=madrid), tick=False):
        run = send_due_digests()
    assert run.sent == 1
    sub.refresh_from_db()
    assert sub.last_digest_count == 1


def test_an_empty_slot_is_recorded_and_shown_in_the_bot(tg):
    import datetime as dt
    from zoneinfo import ZoneInfo

    import time_machine

    from bot.digests import send_due_digests
    from bot.panel import last_digest_line
    from subscriptions.models import Frequency, Subscription

    madrid = ZoneInfo("Europe/Madrid")
    sub = Subscription.objects.create(
        categories=["Python"],
        chat_id=4343,
        linked_at=dt.datetime(2026, 11, 2, 10, 0, tzinfo=madrid),
        frequency=Frequency.EVERY_3H,
        timezone="Europe/Madrid",
    )
    with time_machine.travel(dt.datetime(2026, 11, 2, 18, 2, tzinfo=madrid), tick=False):
        run = send_due_digests()
        sub.refresh_from_db()
        line = last_digest_line(sub)
    assert run.empty == 1 and tg.sent("sendMessage") == []  # nothing new: no message
    assert sub.last_digest_count == 0
    assert line == "📭 Остання перевірка сьогодні о 18:02: нових збігів не було, тож я мовчав."
