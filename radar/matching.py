"""
Keyword matching of vacancies against a subscription's filters.

`matches()` is a pure function on plain values (no DB, no Django models), so the
site, the bot and the digests all share exactly the same rules and it is easy to
test exhaustively.

Rules:
- case-insensitive, over title + description;
- whole-word matching: "java" does not match "javascript", but "c++", "node.js"
  and Cyrillic words work, because boundaries are "not a letter/digit/_" rather
  than regex \\b (which breaks on keywords that start or end with + . #);
- at least one include keyword must match (if any are set);
- none of the exclude keywords may match;
- categories: the vacancy must be listed in at least one chosen category
  (no categories chosen = all categories);
- where: "Віддалено" (remote_only) and cities are one choice with OR: remote
  OR in one of the cities (nothing chosen = anywhere);
- levels: the title must name one of them; a title without a level passes,
  since "Python Developer" can be anything;
- salary_min: the salary range must reach it; vacancies without a salary pass
  only if salary_unknown (most DOU vacancies don't name one).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from .attributes import cities_of, levels_of, salary_reaches

MAX_KEYWORDS = 15
MAX_KEYWORD_LENGTH = 40

_SPLIT_RE = re.compile(r"[,;\n]+")


@dataclass(frozen=True)
class Filters:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    categories: frozenset[str] = frozenset()
    remote_only: bool = False
    cities: frozenset[str] = frozenset()
    levels: frozenset[str] = frozenset()
    salary_min: int = 0
    salary_unknown: bool = True

    @classmethod
    def from_subscription(cls, subscription) -> "Filters":
        return cls(
            include=tuple(subscription.include_keywords or ()),
            exclude=tuple(subscription.exclude_keywords or ()),
            categories=frozenset(subscription.categories or ()),
            remote_only=subscription.remote_only,
            cities=frozenset(subscription.cities or ()),
            levels=frozenset(subscription.levels or ()),
            salary_min=subscription.salary_min or 0,
            salary_unknown=subscription.salary_unknown,
        )


def parse_keywords(raw: str | Iterable[str]) -> list[str]:
    """
    "Python, Django;  django , Junior" -> ["python", "django", "junior"]
    Splits on commas, semicolons and newlines, lowercases, collapses spaces and
    drops empties and duplicates (order kept). Length limits are enforced by the form.
    """
    parts = _SPLIT_RE.split(raw) if isinstance(raw, str) else raw
    seen: dict[str, None] = {}
    for part in parts:
        keyword = " ".join(part.split()).lower()
        if keyword:
            seen.setdefault(keyword, None)
    return list(seen)


@lru_cache(maxsize=512)
def compile_keywords(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    """One regex for a set of keywords, e.g. (?<![\\w])(?:python|c\\+\\+)(?![\\w])."""
    if not keywords:
        return None
    alternatives = sorted(
        (r"\s+".join(re.escape(word) for word in kw.split()) for kw in keywords),
        key=len,
        reverse=True,  # longest first, so "react native" wins over "react"
    )
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE)


def matches(
    filters: Filters,
    *,
    title: str,
    description: str,
    is_remote: bool,
    categories: Iterable[str],
    location: str = "",
    salary_text: str = "",
) -> bool:
    if filters.categories and filters.categories.isdisjoint(categories):
        return False
    if (filters.remote_only or filters.cities) and not _place_matches(filters, is_remote, location):
        return False
    if filters.levels:
        levels = levels_of(title)
        if levels and levels.isdisjoint(filters.levels):
            return False
    if filters.salary_min:
        reaches = salary_reaches(salary_text, filters.salary_min)
        if reaches is False or (reaches is None and not filters.salary_unknown):
            return False

    text = f"{title}\n{description}"
    include = compile_keywords(filters.include)
    if include is not None and not include.search(text):
        return False
    exclude = compile_keywords(filters.exclude)
    if exclude is not None and exclude.search(text):
        return False
    return True


def _place_matches(filters: Filters, is_remote: bool, location: str) -> bool:
    if filters.remote_only and is_remote:
        return True
    wanted = {city.lower() for city in filters.cities}
    return any(city.lower() in wanted for city in cities_of(location))


def vacancy_matches(filters: Filters, vacancy) -> bool:
    """Adapter for Vacancy model instances (prefetch `sources` to avoid N+1)."""
    return matches(
        filters,
        title=vacancy.title,
        description=vacancy.description_text,
        is_remote=vacancy.is_remote,
        categories=vacancy.categories,
        location=vacancy.location,
        salary_text=vacancy.salary_text,
    )


def matched_keywords(filters: Filters, *, title: str, description: str) -> list[str]:
    """Which include keywords a vacancy matched, in the user's order (for card tags)."""
    text = f"{title}\n{description}"
    return [kw for kw in filters.include if compile_keywords((kw,)).search(text)]
