import itertools
import json
from datetime import timedelta

import httpx
import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone

from bot import texts
from bot.models import ProcessedUpdate
from bot.telegram import TelegramClient, TelegramError
from radar.locks import FETCH_LOCK, FETCH_LOCK_TTL, acquire
from radar.models import Source
from subscriptions.models import Delivery, Frequency, Subscription

pytestmark = pytest.mark.django_db

CHAT = 5550001
_update_ids = itertools.count(1000)


# --- helpers -----------------------------------------------------------------------------


def message_update(text: str, chat_id: int = CHAT, chat_type: str = "private") -> dict:
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id},
            "text": text,
        },
    }


def callback_update(data: str, chat_id: int = CHAT, message_id: int = 77) -> dict:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": "cb1",
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
        },
    }


def post_update(client, update: dict, secret: str | None = "test-webhook-secret"):
    headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": secret} if secret is not None else {}
    return client.post(
        reverse("telegram_webhook"),
        data=json.dumps(update),
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def send(client, tg):
    """Deliver an update to the webhook like Telegram would."""

    def _send(update: dict):
        response = post_update(client, update)
        assert response.status_code == 200
        return tg

    return _send


@pytest.fixture
def sub():
    return Subscription.objects.create(include_keywords=["python"], categories=["Python"])


@pytest.fixture
def linked(sub):
    sub.chat_id = CHAT
    sub.linked_at = timezone.now() - timedelta(hours=1)
    sub.frequency = Frequency.EVERY_3H
    sub.save()
    return sub


def fresh_sources():
    Source.objects.update(last_fetched_at=timezone.now())


# --- webhook security -------------------------------------------------------------------


def test_webhook_rejects_wrong_or_missing_secret(client, tg):
    assert post_update(client, message_update("/help"), secret="nope").status_code == 403
    assert post_update(client, message_update("/help"), secret=None).status_code == 403
    assert tg.calls == []


def test_webhook_rejects_everything_when_secret_not_configured(client, tg, settings):
    settings.TELEGRAM_WEBHOOK_SECRET = ""
    assert post_update(client, message_update("/help"), secret="").status_code == 403


def test_webhook_only_accepts_post(client):
    assert client.get(reverse("telegram_webhook")).status_code == 405


def test_webhook_bad_json(client):
    response = client.post(
        reverse("telegram_webhook"),
        data="not json",
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-webhook-secret",
    )
    assert response.status_code == 400


def test_retried_update_is_handled_once(client, tg):
    update = message_update("/help")
    post_update(client, update)
    post_update(client, update)

    assert len(tg.sent()) == 1
    assert ProcessedUpdate.objects.filter(update_id=update["update_id"]).exists()


def test_handler_error_still_answers_200(client, tg, monkeypatch):
    from bot import handlers

    def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(handlers, "send_help", boom)
    assert post_update(client, message_update("/help")).status_code == 200


# --- /start -----------------------------------------------------------------------------


def test_start_with_token_links_chat_and_sends_welcome_batch_and_frequency(send, sub, make_vacancy):
    for i in range(7):
        make_vacancy(title=f"Python dev {i}")
    make_vacancy(title="Java dev")

    tg = send(message_update(f"/start {sub.token}"))

    sub.refresh_from_db()
    assert sub.chat_id == CHAT and sub.linked_at is not None
    welcome, batch, frequency = tg.sent()
    assert "Радар підключено" in welcome["text"] and "python" in welcome["text"]
    assert welcome["reply_markup"]["keyboard"][0][0]["text"] == texts.BTN_CHECK
    assert welcome["reply_markup"]["is_persistent"] is True
    assert "5 з 7" in batch["text"] and batch["text"].count("<a href=") == 5
    assert "Java dev" not in batch["text"]
    assert batch["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "more:5"
    assert batch["parse_mode"] == "HTML"
    assert "Як часто надсилати нове?" in frequency["text"]
    rows = frequency["reply_markup"]["inline_keyboard"]
    assert len(rows) == 5  # 3 rows of 2 + quiet hours + close
    assert rows[-1][0]["callback_data"] == "close"
    assert Delivery.objects.filter(subscription=sub).count() == 5


def test_start_with_no_matches(send, sub):
    tg = send(message_update(f"/start {sub.token}"))
    assert texts.FIRST_BATCH_EMPTY in tg.texts()


def test_start_unknown_token(send):
    tg = send(message_update("/start nope-nope"))
    assert "Не знайшов такий радар" in tg.last_text()
    assert Subscription.objects.filter(chat_id=CHAT).count() == 0


def test_start_without_token(send, settings):
    tg = send(message_update("/start"))
    assert "Щоб почати" in tg.last_text() and settings.SITE_URL + "/filters/" in tg.last_text()


def test_start_again_in_same_chat_does_not_resend_batch(send, linked, make_vacancy):
    make_vacancy(title="Python dev")
    tg = send(message_update(f"/start {linked.token}"))
    assert len(tg.sent()) == 1 and "Ти вже підключений" in tg.last_text()


def test_start_moves_chat_from_another_radar(send, linked):
    other = Subscription.objects.create(include_keywords=["django"])

    send(message_update(f"/start {other.token}"))

    linked.refresh_from_db()
    other.refresh_from_db()
    assert linked.chat_id is None and other.chat_id == CHAT


def test_start_is_linked_on_the_site_page(client, send, sub):
    send(message_update(f"/start {sub.token}"))
    status = client.get(reverse("subscriptions:link_status", args=[sub.token]))
    assert status["HX-Redirect"] == reverse("subscriptions:frequency", args=[sub.token])


def test_group_chats_are_refused(send):
    tg = send(message_update("/start abc", chat_type="group"))
    assert tg.last_text() == texts.PRIVATE_ONLY


def test_unlinked_chat_pressing_menu(send, settings):
    tg = send(message_update(texts.BTN_CHECK))
    assert "ще не підключено" in tg.last_text()


# --- check now ---------------------------------------------------------------------------


def test_check_now_sends_only_new_undelivered_matches(send, linked, make_vacancy):
    fresh_sources()
    make_vacancy(title="Python before linking", first_seen_at=linked.linked_at - timedelta(days=1))
    already = make_vacancy(title="Python already sent")
    Delivery.objects.create(subscription=linked, vacancy=already)
    make_vacancy(title="Python brand new")
    make_vacancy(title="Java brand new")

    tg = send(message_update(texts.BTN_CHECK))

    text = tg.last_text()
    assert "1 нова вакансія" in text and "Python brand new" in text
    assert "already sent" not in text and "before linking" not in text and "Java" not in text
    assert Delivery.objects.filter(subscription=linked).count() == 2


def test_check_now_nothing_new_shows_next_notification(send, linked):
    fresh_sources()
    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        tg = send(message_update("/check"))
    assert tg.last_text() == "Нових вакансій немає. Наступне автосповіщення о 18:00."


def test_check_now_rate_limit(send, linked):
    fresh_sources()
    start = timezone.now()
    with time_machine.travel(start, tick=False):
        send(message_update(texts.BTN_CHECK))
    with time_machine.travel(start + timedelta(seconds=18), tick=False):
        tg = send(message_update(texts.BTN_CHECK))
    assert tg.last_text() == "⏳ Наступна перевірка через 42 с."
    with time_machine.travel(start + timedelta(seconds=61), tick=False):
        tg = send(message_update(texts.BTN_CHECK))
    assert "Нових вакансій немає" in tg.last_text()


def test_check_now_fetches_when_data_is_stale(send, linked, monkeypatch):
    from bot import services

    calls = []
    monkeypatch.setattr(services, "fetch_all_sources", lambda sources: calls.append(list(sources)))
    Source.objects.update(last_fetched_at=timezone.now() - timedelta(minutes=11))

    tg = send(message_update(texts.BTN_CHECK))

    assert len(calls) == 1
    assert [s.category for s in calls[0]] == ["Python"]  # only this radar's categories
    assert texts.CHECK_FETCHING in tg.texts()


def test_check_now_does_not_fetch_when_fresh(send, linked, monkeypatch):
    from bot import services

    calls = []
    monkeypatch.setattr(services, "fetch_all_sources", lambda sources: calls.append(sources))
    Source.objects.update(last_fetched_at=timezone.now() - timedelta(minutes=9))

    send(message_update(texts.BTN_CHECK))

    assert calls == []


def test_check_now_skips_fetch_while_another_fetch_runs(send, linked, monkeypatch):
    from bot import services

    calls = []
    monkeypatch.setattr(services, "fetch_all_sources", lambda sources: calls.append(sources))
    acquire(FETCH_LOCK, FETCH_LOCK_TTL)  # cron run in progress

    tg = send(message_update(texts.BTN_CHECK))

    assert calls == []
    assert "Нових вакансій немає" in tg.last_text()


def test_check_now_when_paused_or_off(send, linked):
    fresh_sources()
    linked.paused = True
    linked.save()
    assert texts.CHECK_PAUSED in send(message_update("/check")).last_text()


# --- latest, filters, help, unknown -------------------------------------------------------


def test_latest_ten(send, linked, make_vacancy, settings):
    for i in range(12):
        make_vacancy(title=f"Python role {i}")

    tg = send(message_update(texts.BTN_LATEST))

    text = tg.last_text()
    assert "Останні 10 збігів" in text and "з 12" in text
    assert text.count("<a href=") == 10
    assert "і ще 2 на сайті" in text and f"{settings.SITE_URL}/s/{linked.token}/" in text


def test_vacancy_html_is_escaped(send, linked, make_vacancy):
    make_vacancy(title="Python <script> & co", company="A&B <Ltd>", salary_text="$1000")
    text = send(message_update("/latest")).last_text()
    assert "Python &lt;script&gt; &amp; co" in text and "A&amp;B &lt;Ltd&gt;" in text


def test_site_link_is_a_button_on_https(send, linked, settings):
    settings.SITE_URL = "https://radar.example.com"
    payload = send(message_update(texts.BTN_FILTERS_MENU)).sent()[-1]
    urls = [b.get("url") for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert f"https://radar.example.com/s/{linked.token}/" in urls


def test_no_site_link_on_localhost(send, linked, settings):
    """Telegram rejects localhost URL buttons, and a bare link adds nothing to the panel."""
    settings.SITE_URL = "http://localhost:8000"
    payload = send(message_update("/filters")).sent()[-1]
    buttons = [b for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert not any("url" in b for b in buttons)
    assert "localhost" not in payload["text"]


def test_help_and_unknown(send, linked):
    assert "Що я вмію" in send(message_update("/help")).last_text()
    assert send(message_update("hello?")).last_text() == texts.UNKNOWN


# --- frequency, quiet hours, pause --------------------------------------------------------


def test_frequency_menu_marks_current_choice(send, linked):
    payload = send(message_update(texts.BTN_FREQUENCY)).sent()[-1]
    labels = [b["text"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]
    assert "✅ Кожні 3 год" in labels
    assert sum(label.startswith("✅") for label in labels) == 1


def test_choosing_frequency_updates_subscription_and_message(send, linked):
    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        tg = send(callback_update("freq:every_12h", message_id=77))

    linked.refresh_from_db()
    assert linked.frequency == Frequency.EVERY_12H
    edit = tg.sent("editMessageText")[-1]
    assert edit["message_id"] == 77
    assert "Кожні 12 год" in edit["text"] and "о 21:00 (Kyiv)" in edit["text"]
    assert tg.sent("answerCallbackQuery")[-1]["text"] == texts.FREQUENCY_SAVED


def test_stale_site_tab_does_not_overwrite_bot_changes(client, send, linked):
    """Site tab opened with 'every 3 h'; the bot then switched to hourly; the old tab
    now turns quiet hours off. Only quiet hours may change."""
    send(callback_update("freq:hourly"))

    client.post(
        reverse("subscriptions:notifications", args=[linked.token]),
        {
            "changed": "quiet_hours_enabled",
            "frequency": "every_3h",  # stale value still shown in the old tab
            "quiet_hours_start": "22",
            "quiet_hours_end": "8",
            # quiet_hours_enabled unchecked -> not sent
        },
    )

    linked.refresh_from_db()
    assert linked.frequency == Frequency.HOURLY
    assert linked.quiet_hours_enabled is False


def test_frequency_choice_shows_on_site(client, send, linked):
    send(callback_update("freq:hourly"))
    page = client.get(reverse("subscriptions:radar_bot", args=[linked.token])).content.decode()
    assert 'value="hourly" checked' in page


def test_pressing_selected_frequency_again_is_fine(send, linked, tg):
    tg.fail_next("editMessageText", 400, "Bad Request: message is not modified")
    send(callback_update("freq:every_3h"))
    assert tg.sent("answerCallbackQuery")


def test_quiet_hours_button_switches_to_what_it_says(send, linked):
    payload = send(message_update(texts.BTN_FREQUENCY)).sent()[-1]
    assert payload["reply_markup"]["inline_keyboard"][-2][0]["callback_data"] == "quiet:off"

    tg = send(callback_update("quiet:off"))
    linked.refresh_from_db()
    assert linked.quiet_hours_enabled is False
    assert texts.QUIET_OFF in tg.sent("editMessageText")[-1]["text"]


def test_frequency_off_text(send, linked):
    tg = send(callback_update("freq:off"))
    assert "Перевіряй вручну" in tg.sent("editMessageText")[-1]["text"]


def test_garbage_callback(send, linked):
    tg = send(callback_update("freq:every_minute"))
    linked.refresh_from_db()
    assert linked.frequency == Frequency.EVERY_3H
    assert tg.sent("answerCallbackQuery")[-1]["text"] == texts.CALLBACK_EXPIRED


def test_callback_from_unlinked_chat(send):
    tg = send(callback_update("freq:daily", chat_id=999))
    assert tg.sent("answerCallbackQuery")[-1]["text"] == texts.CALLBACK_EXPIRED


def test_old_quiet_button_does_not_flip_back_a_change_made_on_the_site(send, linked):
    """The message said 'увімкнено' (button = turn off); the site already turned them off."""
    linked.quiet_hours_enabled = False
    linked.save()

    send(callback_update("quiet:off"))

    linked.refresh_from_db()
    assert linked.quiet_hours_enabled is False  # a toggle would have turned them back on


def test_old_pause_button_does_not_resume(send, linked):
    linked.paused = True
    linked.save()

    send(message_update(texts.BTN_PAUSE))  # an outdated keyboard still shows "Пауза"

    linked.refresh_from_db()
    assert linked.paused is True


def test_pause_command_toggles(send, linked):
    send(message_update("/pause"))
    linked.refresh_from_db()
    assert linked.paused is True
    send(message_update("/pause"))
    linked.refresh_from_db()
    assert linked.paused is False


def test_pause_and_resume_swap_the_menu_button(send, linked):
    tg = send(message_update(texts.BTN_PAUSE))
    linked.refresh_from_db()
    assert linked.paused is True
    assert tg.sent()[-1]["reply_markup"]["keyboard"][2][0]["text"] == texts.BTN_RESUME

    tg = send(message_update(texts.BTN_RESUME))
    linked.refresh_from_db()
    assert linked.paused is False
    assert "Сповіщення відновлено" in tg.last_text()


# --- show more -------------------------------------------------------------------------------


def test_show_more_sends_next_batch_and_removes_old_button(send, linked, make_vacancy):
    for i in range(12):
        make_vacancy(title=f"Python role {i}")

    tg = send(callback_update("more:5", message_id=88))

    assert tg.sent("editMessageReplyMarkup")[-1]["message_id"] == 88
    batch = tg.sent()[-1]
    assert "6–10 з 12" in batch["text"] and batch["text"].startswith("🎯")
    assert "6. <a href=" in batch["text"]
    assert batch["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "more:10"


def test_last_batch_has_no_more_button(send, linked, make_vacancy):
    for i in range(7):
        make_vacancy(title=f"Python role {i}")
    batch = send(callback_update("more:5")).sent()[-1]
    assert "reply_markup" not in batch


# --- stop & blocking --------------------------------------------------------------------------


def test_stop_unlinks_and_removes_menu(send, linked):
    tg = send(message_update("/stop"))
    linked.refresh_from_db()
    assert linked.chat_id is None
    assert tg.sent()[-1]["reply_markup"] == {"remove_keyboard": True}


def test_blocking_the_bot_unlinks(send, linked):
    send(
        {
            "update_id": next(_update_ids),
            "my_chat_member": {
                "chat": {"id": CHAT, "type": "private"},
                "new_chat_member": {"status": "kicked"},
            },
        }
    )
    linked.refresh_from_db()
    assert linked.chat_id is None


def test_403_from_telegram_unlinks_chat(send, linked, tg):
    tg.fail_next("sendMessage", 403, "Forbidden: bot was blocked by the user")
    send(message_update("/help"))
    linked.refresh_from_db()
    assert linked.chat_id is None


# --- client ----------------------------------------------------------------------------------


def test_client_raises_without_leaking_token():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient("123:SECRET", transport=httpx.MockTransport(handler))
    with pytest.raises(TelegramError) as info:
        client.send_message(1, "hi")
    assert "SECRET" not in str(info.value)


def test_client_api_error():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "bad"})

    client = TelegramClient("123:x", transport=httpx.MockTransport(handler))
    with pytest.raises(TelegramError, match="bad"):
        client.send_message(1, "hi")


def test_client_requires_token():
    with pytest.raises(TelegramError):
        TelegramClient("")


# --- management commands ------------------------------------------------------------------------


def test_set_telegram_webhook(tg, settings, capsys):
    from django.core.management import call_command

    settings.SITE_URL = "https://radar.example.com"
    call_command("set_telegram_webhook")

    webhook = tg.sent("setWebhook")[0]
    assert webhook["url"] == "https://radar.example.com/telegram/webhook/"
    assert webhook["secret_token"] == "test-webhook-secret"
    assert webhook["allowed_updates"] == ["message", "callback_query", "my_chat_member"]
    commands = tg.sent("setMyCommands")[0]["commands"]
    assert {"command": "check", "description": "Перевірити нові вакансії зараз"} in commands
    assert "Webhook set for @job_radar_test_bot" in capsys.readouterr().out


def test_set_telegram_webhook_refuses_http(tg, settings):
    from django.core.management import CommandError, call_command

    settings.SITE_URL = "http://localhost:8000"
    with pytest.raises(CommandError, match="HTTPS"):
        call_command("set_telegram_webhook")
    assert tg.sent("setWebhook") == []


def test_run_bot_locally_handles_updates_with_same_handlers(tg, sub):
    from django.core.management import call_command

    tg.webhook_url = "https://old.example.com/telegram/webhook/"
    tg.updates = [message_update(f"/start {sub.token}")]

    call_command("run_bot_locally", once=True)

    sub.refresh_from_db()
    assert tg.sent("deleteWebhook")
    assert sub.chat_id == CHAT


def test_run_bot_locally_retries_idle_timeouts_quietly(tg, monkeypatch, capsys):
    from django.core.management import call_command

    from bot.management.commands import run_bot_locally

    sleeps = []
    monkeypatch.setattr(run_bot_locally.time, "sleep", sleeps.append)
    tg.fail_next("getUpdates", None, "ReadTimeout")

    call_command("run_bot_locally", once=True)

    assert len(tg.sent("getUpdates")) == 2  # failed once, asked again
    assert sleeps == [1]
    assert "failed" not in capsys.readouterr().err


def test_run_bot_locally_reports_being_offline(tg, monkeypatch, capsys):
    from django.core.management import call_command

    from bot.management.commands import run_bot_locally

    sleeps = []
    monkeypatch.setattr(run_bot_locally.time, "sleep", sleeps.append)
    tg.fail_next("getUpdates", None, "ConnectError")

    call_command("run_bot_locally", once=True)

    assert sleeps == [5]
    assert "getUpdates failed" in capsys.readouterr().err


# --- site -> bot: the settings message follows changes made on the site ------------------


def site_post(client, sub, **data):
    return client.post(reverse("subscriptions:notifications", args=[sub.token]), data)


def panel_state():
    from bot.models import ChatState

    return ChatState.objects.filter(chat_id=CHAT).first()


def test_bot_remembers_its_panel(send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    state = panel_state()
    assert state.panel_message_id == tg._next_id  # the id Telegram gave that message
    assert state.panel_screen == "freq"


def test_site_change_edits_the_settings_message_in_telegram(client, send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    message_id = tg._next_id

    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        site_post(client, linked, changed="frequency", frequency="every_12h")

    edit = tg.sent("editMessageText")[-1]
    assert edit["message_id"] == message_id
    assert "Кожні 12 год" in edit["text"] and "о 21:00" in edit["text"]
    labels = [b["text"] for row in edit["reply_markup"]["inline_keyboard"] for b in row]
    assert "✅ Кожні 12 год" in labels


def test_site_quiet_hours_change_updates_the_button(client, send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    site_post(client, linked, changed="quiet_hours_enabled")  # unchecked
    quiet_button = tg.sent("editMessageText")[-1]["reply_markup"]["inline_keyboard"][-2][0]
    assert quiet_button == {"text": texts.QUIET_BUTTON_OFF, "callback_data": "quiet:on"}


def test_site_filter_change_updates_the_panel(client, send, linked):
    tg = send(message_update("/filters"))
    panel_id = tg._next_id
    client.post(
        reverse("subscriptions:detail", args=[linked.token]),
        {"include_keywords": "django, fastapi", "categories": ["Python"]},
    )
    edit = tg.sent("editMessageText")[-1]
    assert "django, fastapi" in edit["text"] and edit["message_id"] == panel_id


def test_step3_save_also_updates_telegram(client, send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    client.post(
        reverse("subscriptions:frequency", args=[linked.token]),
        {"frequency": "daily", "quiet_hours_start": "22", "quiet_hours_end": "8"},
    )
    assert "Раз на день" in tg.sent("editMessageText")[-1]["text"]


def test_no_panel_means_no_telegram_call(client, linked, tg):
    site_post(client, linked, changed="frequency", frequency="hourly")
    linked.refresh_from_db()
    assert linked.frequency == Frequency.HOURLY
    assert tg.sent("editMessageText") == []


def test_deleted_panel_is_forgotten(client, send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    tg.fail_next("editMessageText", 400, "Bad Request: message to edit not found")

    site_post(client, linked, changed="frequency", frequency="hourly")

    linked.refresh_from_db()
    assert linked.frequency == Frequency.HOURLY  # the site still saved
    assert panel_state().panel_message_id is None


def test_telegram_outage_does_not_break_the_site(client, send, linked):
    tg = send(message_update(texts.BTN_FREQUENCY))
    tg.fail_next("editMessageText", 502, "Bad Gateway")

    response = site_post(client, linked, changed="frequency", frequency="hourly")

    linked.refresh_from_db()
    assert response.status_code == 200 and linked.frequency == Frequency.HOURLY
    assert panel_state().panel_message_id is not None  # kept: it may work next time


def test_pressing_buttons_in_older_message_makes_it_the_panel(send, linked):
    send(callback_update("freq:daily", message_id=55))
    assert panel_state().panel_message_id == 55


def test_site_forms_save_only_their_own_columns(client, linked):
    """The bot paused the radar after the page loaded; saving filters must keep the pause."""
    from subscriptions.forms import FiltersForm

    stale = Subscription.objects.get(pk=linked.pk)
    Subscription.objects.filter(pk=linked.pk).update(paused=True)
    form = FiltersForm({"include_keywords": "django"})
    assert form.is_valid()
    form.save(stale)

    linked.refresh_from_db()
    assert linked.paused is True and linked.include_keywords == ["django"]
