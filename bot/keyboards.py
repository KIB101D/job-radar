"""Telegram keyboards outside the settings panel (that one lives in bot/panel.py)."""

from . import texts


def main_menu(subscription) -> dict:
    """The persistent reply keyboard. Pause/resume shows the action that's possible."""
    pause = texts.BTN_RESUME if subscription.paused else texts.BTN_PAUSE
    return {
        "keyboard": [
            [{"text": texts.BTN_CHECK}, {"text": texts.BTN_LATEST}],
            [{"text": texts.BTN_SETTINGS}, {"text": pause}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": texts.MENU_PLACEHOLDER,
    }


REMOVE_MENU = {"remove_keyboard": True}


def more_keyboard(next_offset: int) -> dict:
    return {
        "inline_keyboard": [[{"text": texts.MORE_BUTTON, "callback_data": f"more:{next_offset}"}]]
    }
