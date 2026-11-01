"""
Site -> bot direction of the sync. When filters, frequency or quiet hours are
saved on the website, the settings panel in the chat is re-rendered in place,
so what Telegram shows matches the site. (Bot -> site needs nothing: the site
reads the database.)
"""

from subscriptions.models import Subscription

from .panel import refresh_panel


def refresh_settings_message(subscription: Subscription) -> bool:
    """Best effort: a Telegram hiccup must never break saving on the site."""
    from django.conf import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    return refresh_panel(subscription)
