"""
Polite fetching of RSS sources and saving vacancies to the DB.

Politeness rules:
- custom User-Agent and a timeout on every request;
- sources are fetched one by one with a pause in between (no parallel hammering);
- retries with exponential backoff ONLY for timeouts, 5xx and 429
  (a 404 or 403 will not fix itself, so retrying it just adds load);
- Retry-After is respected for 429/503.

This module is only ever called from background jobs (management commands),
never while rendering a page.
"""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from radar.models import Source, Vacancy

from .parser import ParsedVacancy, parse_feed

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 60.0

# Fields refreshed when a known vacancy shows up in the feed again.
UPDATABLE_FIELDS = (
    "title",
    "company",
    "url",
    "description_text",
    "location",
    "is_remote",
    "salary_text",
    "published_at",
)

Sleep = Callable[[float], None]


class FetchError(Exception):
    pass


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0


@dataclass
class SourceResult:
    source: Source
    ok: bool
    created: int = 0
    updated: int = 0
    error: str = ""


def build_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        headers={
            "User-Agent": settings.FETCH_USER_AGENT,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.5",
        },
        timeout=settings.FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
    )


def fetch_url(
    client: httpx.Client,
    url: str,
    *,
    max_retries: int | None = None,
    sleep: Sleep = time.sleep,
) -> httpx.Response:
    """GET with retries for transient failures only. Raises FetchError otherwise."""
    if max_retries is None:
        max_retries = settings.FETCH_MAX_RETRIES

    for attempt in range(max_retries + 1):
        is_last = attempt == max_retries
        try:
            response = client.get(url)
        except httpx.TimeoutException as exc:
            if is_last:
                raise FetchError(f"Timeout after {attempt + 1} attempts: {url}") from exc
            delay = _backoff(attempt)
            logger.warning("Timeout fetching %s, retry in %.1fs", url, delay)
            sleep(delay)
            continue
        except httpx.HTTPError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

        if response.status_code in RETRYABLE_STATUS:
            if is_last:
                raise FetchError(f"HTTP {response.status_code} after {attempt + 1} attempts: {url}")
            delay = _retry_after(response) or _backoff(attempt)
            logger.warning("HTTP %s from %s, retry in %.1fs", response.status_code, url, delay)
            sleep(delay)
            continue

        if response.is_error:
            raise FetchError(f"HTTP {response.status_code}: {url}")
        return response

    raise AssertionError("unreachable")  # pragma: no cover


def _backoff(attempt: int) -> float:
    return BACKOFF_BASE_SECONDS * (2**attempt)  # 1s, 2s, 4s, ...


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "")
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None  # HTTP-date form or missing: fall back to our backoff


@transaction.atomic
def ingest(source: Source, items: Iterable[ParsedVacancy]) -> IngestResult:
    """
    Upsert parsed items by guid. New guid -> new Vacancy (first_seen_at = now);
    known guid -> refresh its data and last_seen_at. Being in the feed again also
    means the vacancy is active.
    """
    now = timezone.now()
    by_guid = {item.guid: item for item in items}
    if not by_guid:
        return IngestResult()

    existing = Vacancy.objects.in_bulk(list(by_guid), field_name="guid")
    to_create: list[Vacancy] = []
    to_update: list[Vacancy] = []

    for guid, item in by_guid.items():
        fields = {name: getattr(item, name) for name in UPDATABLE_FIELDS}
        vacancy = existing.get(guid)
        if vacancy is None:
            to_create.append(
                Vacancy(guid=guid, source=source, first_seen_at=now, last_seen_at=now, **fields)
            )
        else:
            for name, value in fields.items():
                setattr(vacancy, name, value)
            vacancy.last_seen_at = now
            vacancy.is_active = True
            to_update.append(vacancy)

    Vacancy.objects.bulk_create(to_create)
    Vacancy.objects.bulk_update(to_update, [*UPDATABLE_FIELDS, "last_seen_at", "is_active"])
    return IngestResult(created=len(to_create), updated=len(to_update))


def fetch_source(
    source: Source, client: httpx.Client, *, sleep: Sleep = time.sleep
) -> SourceResult:
    """Fetch, parse and save one source. Errors are recorded on the Source, not raised."""
    try:
        response = fetch_url(client, source.feed_url, sleep=sleep)
        items = parse_feed(response.content)
        result = ingest(source, items)
    except Exception as exc:  # one broken source must not stop the others
        error = f"{type(exc).__name__}: {exc}"[:2000]
        logger.error("Source %s failed: %s", source.name, error)
        source.last_error = error
        source.save(update_fields=["last_error"])
        return SourceResult(source=source, ok=False, error=error)

    source.last_fetched_at = timezone.now()
    source.last_error = ""
    source.save(update_fields=["last_fetched_at", "last_error"])
    logger.info("Source %s: %d new, %d updated", source.name, result.created, result.updated)
    return SourceResult(source=source, ok=True, created=result.created, updated=result.updated)


def fetch_all_sources(
    sources: Iterable[Source] | None = None,
    *,
    client: httpx.Client | None = None,
    sleep: Sleep = time.sleep,
) -> list[SourceResult]:
    """Fetch enabled sources sequentially, pausing between them."""
    if sources is None:
        sources = Source.objects.filter(is_enabled=True)
    sources = list(sources)

    own_client = client is None
    client = client or build_client()
    results: list[SourceResult] = []
    try:
        for index, source in enumerate(sources):
            if index > 0 and settings.FETCH_DELAY_BETWEEN_SOURCES_SECONDS > 0:
                sleep(settings.FETCH_DELAY_BETWEEN_SOURCES_SECONDS)
            results.append(fetch_source(source, client, sleep=sleep))
    finally:
        if own_client:
            client.close()
    return results
