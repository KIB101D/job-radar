"""Vacancy lists as Telegram HTML messages (also used by digests)."""

from html import escape

from django.conf import settings
from django.urls import reverse

from radar.matching import Filters

from . import texts

MESSAGE_LIMIT = 4096
SAFE_LIMIT = 3900  # headroom for the header and the "…і ще N" line
MAX_ITEMS = 15
TITLE_MAX = 120


def radar_url(subscription) -> str:
    return settings.SITE_URL + reverse("subscriptions:detail", args=[subscription.token])


def is_public_url(url: str) -> bool:
    """Telegram rejects inline buttons that point to localhost or plain http."""
    return url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url


def vacancy_item(number: int, vacancy) -> str:
    title = (
        vacancy.title if len(vacancy.title) <= TITLE_MAX else vacancy.title[: TITLE_MAX - 1] + "…"
    )
    line = f'{number}. <a href="{escape(vacancy.url)}">{escape(title)}</a>'
    meta = []
    if vacancy.company:
        meta.append(escape(vacancy.company))
    if vacancy.is_remote:
        meta.append(texts.REMOTE_MARK)
    if vacancy.salary_text:
        meta.append(f"💰 {escape(vacancy.salary_text)}")
    return f"{line}\n{' · '.join(meta)}" if meta else line


def vacancy_list(
    header: str, vacancies, *, start: int = 1, extra: int = 0, more_url: str = ""
) -> tuple[str, int]:
    """
    Header + numbered vacancies, at most MAX_ITEMS and under Telegram's 4096 chars.
    `extra` is how many more exist beyond `vacancies` (for the "…і ще N" line).
    Returns (text, how many vacancies made it into the message).
    """
    parts = [header]
    shown = 0
    for offset, vacancy in enumerate(list(vacancies)[:MAX_ITEMS]):
        item = vacancy_item(start + offset, vacancy)
        if len("\n\n".join([*parts, item])) > SAFE_LIMIT:
            break
        parts.append(item)
        shown += 1
    hidden = len(list(vacancies)) - shown + extra
    if hidden > 0 and more_url:
        parts.append(texts.LIST_MORE_ON_SITE.format(count=hidden, url=more_url))
    return "\n\n".join(parts), shown


def filters_summary(filters: Filters) -> str:
    """'python, django · без: senior · Python · тільки віддалено'."""
    parts = []
    if filters.include:
        parts.append(", ".join(filters.include))
    if filters.exclude:
        parts.append(texts.FILTERS_EXCLUDE.format(words=", ".join(filters.exclude)))
    if filters.categories:
        parts.append(", ".join(sorted(filters.categories)))
    if filters.remote_only:
        parts.append(texts.FILTERS_REMOTE)
    return escape(" · ".join(parts)) if parts else texts.FILTERS_ALL
