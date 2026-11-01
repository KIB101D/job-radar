"""
Turn Telegram updates into actions. Entry point: handle_update(update).

Every handler reads and writes the same Subscription the website uses, so
changing the frequency here shows up on the site and the other way round.
"""

import logging

from django.conf import settings
from django.urls import reverse

from subscriptions import schedule
from subscriptions.delivery import (
    filters_for,
    link_chat,
    linked_subscription,
    mark_delivered,
    newest_matches,
    unlink_chat,
)
from subscriptions.models import Subscription

from . import keyboards, panel, texts
from .formatting import filters_summary, radar_url, vacancy_list
from .services import check_now
from .telegram import TelegramError, get_client

logger = logging.getLogger(__name__)

FIRST_BATCH_SIZE = 5
MORE_BATCH_SIZE = 5
LATEST_SIZE = 10


def site_url() -> str:
    return settings.SITE_URL + reverse("subscriptions:filters")


# --- entry point -----------------------------------------------------------------------


def handle_update(update: dict) -> None:
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])
    elif "my_chat_member" in update:
        handle_chat_member(update["my_chat_member"])


def handle_message(message: dict) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    if chat_id is None or not text:
        return
    if chat.get("type") != "private":
        get_client().send_message(chat_id, texts.PRIVATE_ONLY)
        return

    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()  # "/start@job_radar_bot" -> "/start"

    if command == "/start":
        start(chat_id, argument.strip())
        return
    if command == "/help":
        send_help(chat_id)
        return

    subscription = linked_subscription(chat_id)
    if subscription is None:
        get_client().send_message(chat_id, texts.NOT_LINKED.format(url=site_url()))
        return

    action = ACTIONS.get(command) or ACTIONS.get(text)
    if action is not None:
        if getattr(action, "wants_message_id", False):
            action(subscription, message.get("message_id"))
        else:
            action(subscription)
        return
    # Plain text while the panel shows a word list = words to add.
    if not text.startswith("/") and panel.handle_text(
        subscription, text, message.get("message_id")
    ):
        return
    get_client().send_message(chat_id, texts.UNKNOWN, keyboards.main_menu(subscription))


# --- /start ------------------------------------------------------------------------------


def start(chat_id: int, token: str) -> None:
    client = get_client()
    if not token:
        subscription = linked_subscription(chat_id)
        if subscription:
            welcome_back(subscription)
        else:
            client.send_message(chat_id, texts.START_NO_TOKEN.format(url=site_url()))
        return

    subscription = Subscription.objects.filter(token=token).first()
    if subscription is None:
        client.send_message(chat_id, texts.START_UNKNOWN_TOKEN.format(url=site_url()))
        return

    if not link_chat(subscription, chat_id):
        welcome_back(subscription)
        return

    # 1. Welcome + the persistent menu, 2. first batch, 3. frequency (in the panel).
    summary = filters_summary(filters_for(subscription))
    client.send_message(
        chat_id, texts.WELCOME.format(filters=summary), keyboards.main_menu(subscription)
    )
    send_batch(subscription, offset=0, size=FIRST_BATCH_SIZE)
    panel.open_panel(subscription, "freq")  # step 3 of the design: choose the frequency


def welcome_back(subscription: Subscription) -> None:
    summary = filters_summary(filters_for(subscription))
    get_client().send_message(
        subscription.chat_id,
        texts.WELCOME_BACK.format(filters=summary),
        keyboards.main_menu(subscription),
    )


def send_batch(subscription: Subscription, *, offset: int, size: int) -> None:
    """First batch or the next one ("Показати ще"); recorded as delivered."""
    page = newest_matches(subscription, offset=offset, limit=size)
    if not page.vacancies:
        if offset == 0:
            get_client().send_message(subscription.chat_id, texts.FIRST_BATCH_EMPTY)
        return
    if offset == 0:
        header = texts.FIRST_BATCH.format(shown=len(page.vacancies), total=page.total)
    else:
        header = texts.MORE_BATCH.format(
            start=offset + 1, end=offset + len(page.vacancies), total=page.total
        )
    text, _ = vacancy_list(header, page.vacancies, start=offset + 1)
    markup = keyboards.more_keyboard(page.next_offset) if page.has_more else None
    get_client().send_message(subscription.chat_id, text, markup)
    mark_delivered(subscription, page.vacancies)


# --- menu actions ---------------------------------------------------------------------------


def do_check(subscription: Subscription) -> None:
    client = get_client()
    chat_id = subscription.chat_id
    result = check_now(
        subscription, on_fetch=lambda: client.send_message(chat_id, texts.CHECK_FETCHING)
    )
    if not result.allowed:
        client.send_message(chat_id, texts.CHECK_WAIT.format(seconds=result.wait_seconds))
        return

    if result.vacancies:
        header = texts.CHECK_FOUND.format(count_text=texts.new_vacancies(len(result.vacancies)))
        text, _ = vacancy_list(header, result.vacancies, more_url=radar_url(subscription))
    else:
        text = f"{texts.CHECK_NOTHING} {next_notification_line(subscription)}"
    client.send_message(chat_id, text, keyboards.main_menu(subscription))


def next_notification_line(subscription: Subscription) -> str:
    if subscription.paused:
        return texts.CHECK_PAUSED
    when = schedule.next_slot_label(subscription)
    return texts.CHECK_NEXT.format(when=when) if when else texts.CHECK_OFF


def do_latest(subscription: Subscription) -> None:
    page = newest_matches(subscription, limit=LATEST_SIZE)
    if not page.vacancies:
        get_client().send_message(subscription.chat_id, texts.LATEST_EMPTY)
        return
    header = texts.LATEST.format(shown=len(page.vacancies), total=page.total)
    text, _ = vacancy_list(
        header,
        page.vacancies,
        extra=page.total - len(page.vacancies),
        more_url=radar_url(subscription),
    )
    get_client().send_message(subscription.chat_id, text)
    mark_delivered(subscription, page.vacancies)


def set_paused(subscription: Subscription, paused: bool) -> None:
    """A new message (an event worth seeing), plus the panel shows the new state."""
    subscription.paused = paused
    subscription.save(update_fields=["paused", "updated_at"])
    if subscription.paused:
        text = texts.PAUSED
    else:
        text = f"{texts.RESUMED}\n{next_notification_line(subscription)}"
    get_client().send_message(subscription.chat_id, text, keyboards.main_menu(subscription))
    panel.refresh_panel(subscription)


def open_screen(screen: str):
    """Menu/command handler that (re)opens the settings panel on `screen`."""

    def handler(subscription: Subscription, message_id: int | None = None) -> None:
        panel.open_panel(subscription, screen, tap_message_id=message_id)

    handler.wants_message_id = True
    return handler


def send_help(chat_id: int) -> None:
    subscription = linked_subscription(chat_id)
    markup = keyboards.main_menu(subscription) if subscription else None
    get_client().send_message(chat_id, texts.HELP, markup)


def stop(subscription: Subscription) -> None:
    chat_id = subscription.chat_id
    unlink_chat(chat_id)
    get_client().send_message(chat_id, texts.STOPPED, keyboards.REMOVE_MENU)


ACTIONS = {
    "/check": do_check,
    texts.BTN_CHECK: do_check,
    "/latest": do_latest,
    texts.BTN_LATEST: do_latest,
    "/filters": open_screen("filters"),
    texts.BTN_FILTERS_MENU: open_screen("filters"),
    "/frequency": open_screen("freq"),
    texts.BTN_NOTIFY_MENU: open_screen("freq"),
    # Buttons of earlier menus, still on screen in chats until the menu refreshes.
    "/settings": open_screen("filters"),
    texts.BTN_SETTINGS: open_screen("filters"),
    texts.BTN_FILTERS: open_screen("filters"),
    texts.BTN_FREQUENCY: open_screen("freq"),
    # Menu buttons are explicit, so an outdated keyboard can't do the opposite.
    "/pause": lambda sub: set_paused(sub, not sub.paused),
    texts.BTN_PAUSE: lambda sub: set_paused(sub, True),
    texts.BTN_RESUME: lambda sub: set_paused(sub, False),
    "/stop": stop,
}


# --- inline buttons ------------------------------------------------------------------------------


def handle_callback(callback: dict) -> None:
    client = get_client()
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    data = callback.get("data") or ""
    subscription = linked_subscription(chat_id) if chat_id is not None else None

    if subscription is None:
        client.answer_callback_query(callback["id"], texts.CALLBACK_EXPIRED)
        return

    kind, _, value = data.partition(":")
    if kind == "more" and value.isdigit():
        client.remove_inline_keyboard(chat_id, message["message_id"])
        send_batch(subscription, offset=int(value), size=MORE_BATCH_SIZE)
        client.answer_callback_query(callback["id"])
        return

    answer = panel.handle_callback(subscription, data, message["message_id"])
    if answer is None:  # not a button we know (old or tampered callback_data)
        answer = texts.CALLBACK_EXPIRED
    client.answer_callback_query(callback["id"], answer or None)


# --- membership ----------------------------------------------------------------------------------


def handle_chat_member(update: dict) -> None:
    """The user blocked the bot: stop treating the chat as linked."""
    status = (update.get("new_chat_member") or {}).get("status")
    chat_id = (update.get("chat") or {}).get("id")
    if status == "kicked" and chat_id is not None:
        unlink_chat(chat_id)
        logger.info("Chat %s blocked the bot; unlinked", chat_id)


def safe_handle_update(update: dict) -> None:
    """handle_update that never raises: a bad update must not break the webhook."""
    try:
        handle_update(update)
    except TelegramError as exc:
        if exc.chat_unavailable:
            chat_id = _chat_id_of(update)
            if chat_id is not None:
                unlink_chat(chat_id)
        logger.warning(
            "Telegram API error while handling update %s: %s", update.get("update_id"), exc
        )
    except Exception:
        logger.exception("Failed to handle update %s", update.get("update_id"))


def _chat_id_of(update: dict) -> int | None:
    if "message" in update:
        return (update["message"].get("chat") or {}).get("id")
    if "callback_query" in update:
        return ((update["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    return None
