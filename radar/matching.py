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
- remote_only keeps only remote vacancies;
- categories: the vacancy must be listed in at least one chosen category
  (no categories chosen = all categories).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

MAX_KEYWORDS = 15
MAX_KEYWORD_LENGTH = 40

_SPLIT_RE = re.compile(r"[,;\n]+")


@dataclass(frozen=True)
class Filters:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    categories: frozenset[str] = frozenset()
    remote_only: bool = False

    @classmethod
    def from_subscription(cls, subscription) -> "Filters":
        return cls(
            include=tuple(subscription.include_keywords or ()),
            exclude=tuple(subscription.exclude_keywords or ()),
            categories=frozenset(subscription.categories or ()),
            remote_only=subscription.remote_only,
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
) -> bool:
    if filters.remote_only and not is_remote:
        return False
    if filters.categories and filters.categories.isdisjoint(categories):
        return False

    text = f"{title}\n{description}"
    include = compile_keywords(filters.include)
    if include is not None and not include.search(text):
        return False
    exclude = compile_keywords(filters.exclude)
    if exclude is not None and exclude.search(text):
        return False
    return True


def vacancy_matches(filters: Filters, vacancy) -> bool:
    """Adapter for Vacancy model instances (prefetch `sources` to avoid N+1)."""
    return matches(
        filters,
        title=vacancy.title,
        description=vacancy.description_text,
        is_remote=vacancy.is_remote,
        categories=vacancy.categories,
    )
