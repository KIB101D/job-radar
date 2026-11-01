"""Vacancy lists as Telegram HTML messages (also used by digests)."""

from html import escape

from django.conf import settings
from django.urls import reverse

from radar.attributes import LEVEL_LABELS, LEVELS
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


def levels_text(levels) -> str:
    """'Junior, Middle' in the usual order, '' for any."""
    return ", ".join(LEVEL_LABELS[level] for level in LEVELS if level in levels)


def places_text(remote: bool, cities) -> str:
    """'віддалено, Київ, Львів', '' for anywhere."""
    return ", ".join([texts.REMOTE] * remote + sorted(cities))


def salary_label(minimum: int, unknown: bool) -> str:
    """'від $1000' or 'від $1000 і без зарплати', '' for any."""
    if not minimum:
        return ""
    salary = texts.SALARY_FROM.format(amount=minimum)
    return texts.SALARY_AND_UNKNOWN.format(salary=salary) if unknown else salary


def filters_summary(filters: Filters) -> str:
    """'python, django · без: senior · Python · Junior · віддалено, Київ · від $1000'."""
    parts = []
    if filters.include:
        parts.append(", ".join(filters.include))
    if filters.exclude:
        parts.append(texts.FILTERS_EXCLUDE.format(words=", ".join(filters.exclude)))
    if filters.categories:
        parts.append(", ".join(sorted(filters.categories)))
    if filters.levels:
        parts.append(levels_text(filters.levels))
    if filters.remote_only and not filters.cities:
        parts.append(texts.FILTERS_REMOTE)
    elif filters.cities:
        parts.append(places_text(filters.remote_only, filters.cities))
    if filters.salary_min:
        parts.append(texts.SALARY_FROM.format(amount=filters.salary_min))
    return escape(" · ".join(parts)) if parts else texts.FILTERS_ALL
