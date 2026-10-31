"""
Find vacancies for a set of filters.

Cheap, indexable conditions (active, remote, category) are filtered in SQL.
Keyword matching needs Unicode-aware word boundaries that SQLite and Postgres
don't share, so it runs in Python via radar.matching on the prefiltered rows.
The vacancy table holds a few thousand rows at most, so this stays fast.
"""

from dataclasses import dataclass, field

from django.db.models import Max

from .matching import Filters, vacancy_matches
from .models import Vacancy

CHUNK_SIZE = 200


@dataclass
class Page:
    vacancies: list[Vacancy] = field(default_factory=list)
    has_more: bool = False
    next_offset: int = 0
    total: int = 0


def base_queryset(filters: Filters):
    qs = Vacancy.objects.active().prefetch_related("sources")
    if filters.remote_only:
        qs = qs.filter(is_remote=True)
    if filters.categories:
        qs = qs.in_categories(filters.categories)
    return qs


def search(filters: Filters, *, offset: int = 0, limit: int = 20) -> Page:
    """Newest matching vacancies, paginated by offset over the matched list."""
    qs = base_queryset(filters).newest_first()
    matched = [v for v in qs.iterator(chunk_size=CHUNK_SIZE) if vacancy_matches(filters, v)]
    page = matched[offset : offset + limit]
    return Page(
        vacancies=page,
        has_more=len(matched) > offset + limit,
        next_offset=offset + len(page),
        total=len(matched),
    )


def newer_than(filters: Filters, since_id: int) -> list[Vacancy]:
    """Matching vacancies that arrived after `since_id` (ids only grow)."""
    qs = base_queryset(filters).filter(id__gt=since_id).newest_first()
    return [v for v in qs if vacancy_matches(filters, v)]


def latest_vacancy_id() -> int:
    return Vacancy.objects.aggregate(m=Max("id"))["m"] or 0


def fresh_feed(limit: int = 10) -> list[Vacancy]:
    """'Свіжі вакансії': the newest vacancies we've seen, regardless of filters."""
    return list(
        Vacancy.objects.active()
        .prefetch_related("sources")
        .order_by("-first_seen_at", "-id")[:limit]
    )
