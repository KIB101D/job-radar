"""The settings panel: one message edited in place, and filter editing in the bot."""

import itertools
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from bot import texts
from bot.models import ChatState
from bot.panel import fingerprint
from subscriptions.models import Frequency, Subscription

pytestmark = pytest.mark.django_db

CHAT = 7770001
_ids = itertools.count(50_000)


def msg(text: str, message_id: int = 9) -> dict:
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": message_id,
            "chat": {"id": CHAT, "type": "private"},
            "text": text,
        },
    }


def tap(data: str, message_id: int) -> dict:
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": "cb",
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": CHAT}},
        },
    }


@pytest.fixture
def send(client, tg):
    def _send(update):
        response = client.post(
            reverse("telegram_webhook"),
            data=json.dumps(update),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="test-webhook-secret",
        )
        assert response.status_code == 200
        return tg

    return _send


@pytest.fixture
def sub():
    return Subscription.objects.create(
        include_keywords=["python"],
        exclude_keywords=["senior"],
        categories=["Python"],
        chat_id=CHAT,
        linked_at=timezone.now(),
        frequency=Frequency.EVERY_3H,
    )


@pytest.fixture
def panel(send, sub):
    """Open the panel; returns its message id."""
    tg = send(msg(texts.BTN_SETTINGS))
    return tg._next_id


def buttons(payload) -> list[dict]:
    return [b for row in payload["reply_markup"]["inline_keyboard"] for b in row]


def labels(payload) -> list[str]:
    return [b["text"] for b in buttons(payload)]


def last_edit(tg) -> dict:
    return tg.sent("editMessageText")[-1]


def state() -> ChatState:
    return ChatState.objects.get(chat_id=CHAT)


# --- one window ------------------------------------------------------------------------------


def test_menu_has_settings_instead_of_separate_buttons(send, sub):
    keyboard = send(msg("/help")).sent()[-1]["reply_markup"]["keyboard"]
    assert keyboard == [
        [{"text": texts.BTN_CHECK}, {"text": texts.BTN_LATEST}],
        [{"text": texts.BTN_SETTINGS}, {"text": texts.BTN_PAUSE}],
    ]


def test_settings_opens_main_screen_with_summary(send, sub, make_vacancy):
    make_vacancy(title="Python dev")
    payload = send(msg(texts.BTN_SETTINGS)).sent()[-1]

    text = payload["text"]
    assert "Налаштування радару" in text
    assert "Слова: python" in text and "Без слів: senior" in text and "Категорії: Python" in text
    assert "зараз збігів: <b>1</b>" in text
    assert "Кожні 3 год" in text
    assert [b.get("callback_data") for b in buttons(payload)][:2] == ["nav:filters", "nav:freq"]


def test_reopening_replaces_the_old_panel_and_cleans_the_tap(send, sub, panel):
    tg = send(msg(texts.BTN_SETTINGS, message_id=321))

    deleted = [p["message_id"] for p in tg.sent("deleteMessage")]
    assert panel in deleted  # the previous panel
    assert 321 in deleted  # the "⚙️ Налаштування" tap itself
    assert state().panel_message_id == tg._next_id


def test_moving_between_screens_edits_the_same_message(send, sub, panel):
    tg = send(tap("nav:filters", panel))
    assert last_edit(tg)["message_id"] == panel and "🔎 <b>Фільтри</b>" in last_edit(tg)["text"]

    send(tap("nav:words", panel))
    assert "Слова для пошуку" in last_edit(tg)["text"]

    send(tap("nav:filters", panel))
    send(tap("nav:main", panel))
    assert "Налаштування радару" in last_edit(tg)["text"]
    assert tg.sent("sendMessage")[-1]["text"].startswith("⚙️")  # no extra messages sent
    assert state().panel_screen == "main"


def test_close_deletes_the_panel(send, sub, panel):
    tg = send(tap("close", panel))
    assert tg.sent("deleteMessage")[-1]["message_id"] == panel
    assert state().panel_message_id is None


def test_old_deleted_panel_is_fine(send, sub, panel, tg):
    tg.fail_next("deleteMessage", 400, "Bad Request: message can't be deleted for everyone")
    send(msg(texts.BTN_SETTINGS))  # still opens a new one
    assert state().panel_message_id == tg._next_id


def test_commands_open_the_right_screen(send, sub):
    assert "🔎 <b>Фільтри</b>" in send(msg("/filters")).sent()[-1]["text"]
    assert "Як часто надсилати нове?" in send(msg("/frequency")).sent()[-1]["text"]


def test_buttons_of_the_old_menu_still_work(send, sub):
    assert "🔎 <b>Фільтри</b>" in send(msg(texts.BTN_FILTERS)).sent()[-1]["text"]


def test_pause_is_a_new_message_and_updates_the_panel(send, sub, panel):
    tg = send(msg(texts.BTN_PAUSE))
    assert "Сповіщення на паузі" in tg.sent("sendMessage")[-1]["text"]
    assert "На паузі" in last_edit(tg)["text"] and last_edit(tg)["message_id"] == panel


# --- words -------------------------------------------------------------------------------------


def test_typing_on_the_words_screen_adds_words(send, sub, panel):
    send(tap("nav:words", panel))

    tg = send(msg("Django, FastAPI, python", message_id=555))

    sub.refresh_from_db()
    assert sub.include_keywords == ["python", "django", "fastapi"]
    assert tg.sent("deleteMessage")[-1]["message_id"] == 555  # the typed message is cleaned up
    edit = last_edit(tg)
    assert edit["message_id"] == panel
    assert "✅ Додано: django, fastapi" in edit["text"]
    assert "✖️ fastapi" in labels(edit)


def test_typing_elsewhere_is_not_treated_as_words(send, sub, panel):
    tg = send(msg("django"))  # panel is on the main screen
    sub.refresh_from_db()
    assert sub.include_keywords == ["python"]
    assert tg.last_text() == texts.UNKNOWN


def test_menu_buttons_win_over_word_input(send, sub, panel, make_vacancy):
    send(tap("nav:words", panel))
    tg = send(msg(texts.BTN_LATEST))
    sub.refresh_from_db()
    assert sub.include_keywords == ["python"]
    assert tg.last_text() == texts.LATEST_EMPTY


def test_word_limits(send, sub, panel):
    send(tap("nav:words", panel))
    tg = send(msg("x" * 41))
    assert "задовге" in last_edit(tg)["text"]

    many = ", ".join(f"w{i}" for i in range(20))
    send(msg(many))
    sub.refresh_from_db()
    assert len(sub.include_keywords) == 15
    assert "Не більше 15 слів" in last_edit(tg)["text"]


def test_duplicate_words_are_ignored(send, sub, panel):
    send(tap("nav:words", panel))
    tg = send(msg("Python"))
    assert texts.NOTE_ALREADY in last_edit(tg)["text"]


def test_tapping_a_word_removes_it(send, sub, panel):
    send(tap("nav:words", panel))
    tg = send(tap(f"kwdel:inc:0:{fingerprint('python')}", panel))
    sub.refresh_from_db()
    assert sub.include_keywords == []
    assert "Прибрано: python" in last_edit(tg)["text"]


def test_stale_word_button_removes_nothing(send, sub, panel):
    """The site replaced the words after the panel was drawn."""
    Subscription.objects.filter(pk=sub.pk).update(include_keywords=["rust"])
    tg = send(tap(f"kwdel:inc:0:{fingerprint('python')}", panel))
    sub.refresh_from_db()
    assert sub.include_keywords == ["rust"]
    assert texts.NOTE_STALE in last_edit(tg)["text"]


def test_exclude_words_and_clear_all(send, sub, panel):
    send(tap("nav:exclude", panel))
    send(msg("lead, php"))
    sub.refresh_from_db()
    assert sub.exclude_keywords == ["senior", "lead", "php"]

    send(tap("kwclr:exc", panel))
    sub.refresh_from_db()
    assert sub.exclude_keywords == []


# --- categories & remote ----------------------------------------------------------------------


def test_categories_toggle_with_absolute_buttons(send, sub, panel):
    tg = send(tap("nav:cats", panel))
    cats = {b["text"]: b["callback_data"] for b in buttons(last_edit(tg))}
    assert cats["✅ Python"] == "cat-:Python" and cats["Front End"] == "cat+:Front End"

    send(tap("cat+:Front End", panel))
    send(tap("cat+:Front End", panel))  # pressing twice keeps it on
    sub.refresh_from_db()
    assert sub.categories == ["Front End", "Python"]

    send(tap("catall", panel))
    sub.refresh_from_db()
    assert sub.categories == []


def test_unknown_category_is_ignored(send, sub, panel):
    send(tap("cat+:Cobol", panel))
    sub.refresh_from_db()
    assert sub.categories == ["Python"]


def test_remote_only_switch(send, sub, panel):
    tg = send(tap("remote:on", panel))
    sub.refresh_from_db()
    assert sub.remote_only is True
    assert texts.BTN_REMOTE_ON in labels(last_edit(tg))
    send(tap("remote:on", panel))  # an old panel pressed again: stays on
    sub.refresh_from_db()
    assert sub.remote_only is True


def test_filters_changed_in_bot_are_used_by_the_site_and_the_bot(
    client, send, sub, panel, make_vacancy
):
    make_vacancy(title="Django developer")
    make_vacancy(title="Python developer")
    send(tap("nav:words", panel))
    send(tap(f"kwdel:inc:0:{fingerprint('python')}", panel))
    tg = send(msg("django"))

    assert "📊 Зараз збігів: <b>1</b>" in last_edit(tg)["text"]
    page = client.get(reverse("subscriptions:detail", args=[sub.token])).content.decode()
    results = page.split('id="results"', 1)[1].split('id="feed"', 1)[0]
    assert "Django developer" in results and "Python developer" not in results


def test_bot_filter_edit_keeps_bot_owned_columns(send, sub, panel):
    """Editing words must not touch frequency or pause (update_fields)."""
    Subscription.objects.filter(pk=sub.pk).update(paused=True, frequency=Frequency.HOURLY)
    send(tap("nav:words", panel))
    send(msg("django"))
    sub.refresh_from_db()
    assert sub.paused is True and sub.frequency == Frequency.HOURLY
