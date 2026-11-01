"""
Site -> bot direction of the sync: when notification settings change on the
website, edit the bot's settings message in the chat so the ✅ and "Наступне
сповіщення о …" there match. (Bot -> site needs nothing: the site reads the DB.)

Only the latest settings message is tracked; older ones may show old ticks, but
their buttons set absolute values, so pressing them is still safe.
"""

import logging

from django.conf import settings

from subscriptions.models import Subscription

from . import keyboards
from .handlers import frequency_text
from .telegram import TelegramError, get_client

logger = logging.getLogger(__name__)

# Telegram's answers when the message is gone or too old to edit.
_GONE = ("message to edit not found", "message can't be edited", "message_id_invalid")


def refresh_settings_message(subscription: Subscription) -> bool:
    """
    Best effort: a Telegram hiccup must never break saving on the site.
    Returns True if the message was edited.
    """
    if not (settings.TELEGRAM_BOT_TOKEN and subscription.chat_id):
        return False
    if not subscription.settings_message_id:
        return False
    try:
        get_client().edit_message_text(
            subscription.chat_id,
            subscription.settings_message_id,
            frequency_text(subscription),
            keyboards.frequency_keyboard(subscription),
        )
    except TelegramError as exc:
        if any(marker in exc.description.lower() for marker in _GONE):
            subscription.settings_message_id = None
            subscription.save(update_fields=["settings_message_id"])
        else:
            logger.warning("Could not update settings message for %s: %s", subscription.pk, exc)
        return False
    return True
