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
from subscriptions.models import Frequency, Subscription

from . import keyboards, texts
from .formatting import filters_summary, is_public_url, radar_url, vacancy_list
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
    if action is None:
        get_client().send_message(chat_id, texts.UNKNOWN, keyboards.main_menu(subscription))
        return
    action(subscription)


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

    # 1. Welcome + the persistent menu, 2. first batch, 3. frequency choice.
    summary = filters_summary(filters_for(subscription))
    client.send_message(
        chat_id, texts.WELCOME.format(filters=summary), keyboards.main_menu(subscription)
    )
    send_batch(subscription, offset=0, size=FIRST_BATCH_SIZE)
    send_frequency(subscription)


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


def send_frequency(subscription: Subscription) -> None:
    get_client().send_message(
        subscription.chat_id,
        frequency_text(subscription),
        keyboards.frequency_keyboard(subscription),
    )


def frequency_text(subscription: Subscription) -> str:
    lines = [
        texts.FREQUENCY_TITLE,
        texts.FREQUENCY_CURRENT.format(label=subscription.get_frequency_display()),
    ]
    when = schedule.next_slot_label(subscription)
    if subscription.paused:
        lines.append(texts.FREQUENCY_PAUSED.format(button=texts.BTN_RESUME))
    elif when:
        lines.append(
            texts.FREQUENCY_NEXT.format(when=when, tz=schedule.timezone_label(subscription))
        )
    else:
        lines.append(texts.FREQUENCY_OFF_HINT.format(button=texts.BTN_CHECK))
    if subscription.frequency != Frequency.OFF:
        if subscription.quiet_hours_enabled:
            lines.append(
                texts.QUIET_ON.format(
                    start=f"{subscription.quiet_hours_start:02d}:00",
                    end=f"{subscription.quiet_hours_end:02d}:00",
                )
            )
        else:
            lines.append(texts.QUIET_OFF)
    return "\n".join(lines)


def toggle_pause(subscription: Subscription) -> None:
    subscription.paused = not subscription.paused
    subscription.save(update_fields=["paused", "updated_at"])
    if subscription.paused:
        text = texts.PAUSED
    else:
        text = f"{texts.RESUMED}\n{next_notification_line(subscription)}"
    get_client().send_message(subscription.chat_id, text, keyboards.main_menu(subscription))


def send_filters_link(subscription: Subscription) -> None:
    url = radar_url(subscription)
    if is_public_url(url):
        get_client().send_message(
            subscription.chat_id,
            texts.FILTERS_LINK,
            keyboards.url_keyboard(texts.FILTERS_BUTTON, url),
        )
    else:  # local development: Telegram won't accept a localhost button
        get_client().send_message(subscription.chat_id, texts.FILTERS_LINK_PLAIN.format(url=url))


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
    "/frequency": send_frequency,
    texts.BTN_FREQUENCY: send_frequency,
    "/pause": toggle_pause,
    texts.BTN_PAUSE: toggle_pause,
    texts.BTN_RESUME: toggle_pause,
    "/filters": send_filters_link,
    texts.BTN_FILTERS: send_filters_link,
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
    answer = None
    if kind == "freq" and value in Frequency.values:
        subscription.frequency = value
        subscription.save(update_fields=["frequency", "updated_at"])
        refresh_frequency_message(subscription, message)
        answer = texts.FREQUENCY_SAVED
    elif kind == "quiet":
        subscription.quiet_hours_enabled = not subscription.quiet_hours_enabled
        subscription.save(update_fields=["quiet_hours_enabled", "updated_at"])
        refresh_frequency_message(subscription, message)
        answer = texts.FREQUENCY_SAVED
    elif kind == "more" and value.isdigit():
        client.remove_inline_keyboard(chat_id, message["message_id"])
        send_batch(subscription, offset=int(value), size=MORE_BATCH_SIZE)
    else:
        answer = texts.CALLBACK_EXPIRED
    client.answer_callback_query(callback["id"], answer)


def refresh_frequency_message(subscription: Subscription, message: dict) -> None:
    get_client().edit_message_text(
        subscription.chat_id,
        message["message_id"],
        frequency_text(subscription),
        keyboards.frequency_keyboard(subscription),
    )


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
