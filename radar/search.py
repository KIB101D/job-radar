"""
Find vacancies for a set of filters.

Cheap, indexable conditions (active, remote, category) are filtered in SQL.
Keyword matching needs Unicode-aware word boundaries that SQLite and Postgres
don't share, so it runs in Python via radar.matching on the prefiltered rows.
The vacancy table holds a few thousand rows at most, so this stays fast.
"""

from dataclasses import dataclass, field

from django.core.cache import cache
from django.db.models import Max

from .attributes import cities_of
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
    if filters.remote_only and not filters.cities:  # with cities it's "remote OR city"
        qs = qs.filter(is_remote=True)
    if filters.categories:
        qs = qs.in_categories(filters.categories)
    return qs


def search(
    filters: Filters,
    *,
    offset: int = 0,
    limit: int = 20,
    fresh_after=None,
    skip_delivered_to=None,
    published_after=None,
) -> Page:
    """
    Newest matching vacancies, paginated by offset over the matched list.

    `fresh_after` + `skip_delivered_to` give the "Нові" view: first seen after
    that moment and not yet sent to that subscription's Telegram chat.
    `published_after` is the site-only "Свіжість" setting.
    """
    qs = base_queryset(filters)
    if fresh_after is not None:
        qs = qs.filter(first_seen_at__gt=fresh_after)
    if published_after is not None:
        qs = qs.filter(published_at__gt=published_after)
    if skip_delivered_to is not None:
        qs = qs.exclude(deliveries__subscription=skip_delivered_to)
    qs = qs.newest_first()
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


POPULAR_CITIES = 12


def popular_cities(limit: int = POPULAR_CITIES) -> list[str]:
    """
    Cities that active vacancies actually list, most common first: the choices of
    the "Місто" filter. One column over a few thousand rows, cached for 10 minutes.
    """
    cached = cache.get("popular_cities")
    if cached is None:
        counts: dict[str, int] = {}
        for location in Vacancy.objects.active().values_list("location", flat=True):
            for city in cities_of(location):
                counts[city] = counts.get(city, 0) + 1
        cached = sorted(counts, key=lambda city: (-counts[city], city))
        cache.set("popular_cities", cached, 600)
    return cached[:limit]
