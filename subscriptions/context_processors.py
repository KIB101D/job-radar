"""Navigation: which page is active and where "Фільтри", "Telegram-бот" and "Дані" lead."""

from django.urls import reverse

# url_name -> nav item it belongs to
_SECTIONS = {
    "home": "home",
    "filters": "filters",
    "detail": "filters",
    "bot": "bot",
    "radar_bot": "bot",
}


def navigation(request) -> dict:
    match = getattr(request, "resolver_match", None)
    if match is None or match.namespace != "subscriptions":
        return {"nav_active": "", "nav_data_url": reverse("subscriptions:data")}

    token = match.kwargs.get("token")
    if token:
        # Inside a radar, stay inside it.
        filters_url = reverse("subscriptions:detail", args=[token])
        bot_url = reverse("subscriptions:radar_bot", args=[token])
        data_url = reverse("subscriptions:radar_data", args=[token])
    else:
        # /filters/ and /bot/ redirect to the radar from the cookie, if any.
        filters_url = reverse("subscriptions:filters")
        bot_url = reverse("subscriptions:bot")
        data_url = reverse("subscriptions:data")

    return {
        "nav_active": _SECTIONS.get(match.url_name, ""),
        "nav_filters_url": filters_url,
        "nav_bot_url": bot_url,
        "nav_data_url": data_url,
    }
