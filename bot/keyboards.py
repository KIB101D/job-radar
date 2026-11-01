"""Telegram keyboards: a persistent reply menu and small inline keyboards."""

from subscriptions.models import Frequency

from . import texts


def main_menu(subscription) -> dict:
    pause = texts.BTN_RESUME if subscription.paused else texts.BTN_PAUSE
    return {
        "keyboard": [
            [{"text": texts.BTN_CHECK}, {"text": texts.BTN_LATEST}],
            [{"text": texts.BTN_FREQUENCY}, {"text": pause}],
            [{"text": texts.BTN_FILTERS}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": texts.MENU_PLACEHOLDER,
    }


REMOVE_MENU = {"remove_keyboard": True}


def frequency_keyboard(subscription) -> dict:
    """Six options, two per row, the current one ticked; quiet-hours toggle below."""
    buttons = [
        {
            "text": (texts.SELECTED if value == subscription.frequency else "") + label,
            "callback_data": f"freq:{value}",
        }
        for value, label in Frequency.choices
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    quiet = texts.QUIET_BUTTON_ON if subscription.quiet_hours_enabled else texts.QUIET_BUTTON_OFF
    rows.append([{"text": quiet, "callback_data": "quiet:toggle"}])
    return {"inline_keyboard": rows}


def more_keyboard(next_offset: int) -> dict:
    return {
        "inline_keyboard": [[{"text": texts.MORE_BUTTON, "callback_data": f"more:{next_offset}"}]]
    }


def url_keyboard(text: str, url: str) -> dict:
    return {"inline_keyboard": [[{"text": text, "url": url}]]}
