from dataclasses import replace
from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from radar.models import Vacancy
from radar.services.fetcher import ingest
from radar.services.parser import parse_feed

pytestmark = pytest.mark.django_db


@pytest.fixture
def items(load_fixture):
    return parse_feed(load_fixture("dou_python.xml"))


def test_first_ingest_creates_everything(python_source, items):
    result = ingest(python_source, items)

    assert (result.created, result.updated) == (25, 0)
    assert Vacancy.objects.count() == 25
    v = Vacancy.objects.get(guid__contains="/vacancies/378476/")
    assert v.source == python_source
    assert v.is_active and v.is_remote
    assert v.first_seen_at == v.last_seen_at


def test_second_ingest_dedupes_by_guid(python_source, items):
    ingest(python_source, items)
    result = ingest(python_source, items)

    assert (result.created, result.updated) == (0, 25)
    assert Vacancy.objects.count() == 25


def test_bumped_vacancy_is_updated_not_duplicated(python_source, items, load_fixture):
    """DOU bumps change the <guid> timestamp; the canonical guid stays the same."""
    ingest(python_source, items)
    original = Vacancy.objects.get(guid__contains="/vacancies/378476/")

    raw = load_fixture("dou_python.xml").replace(b"378476/?1793441479", b"378476/?1799999999")
    bumped = [replace(i, title="Renamed") if "378476" in i.guid else i for i in parse_feed(raw)]
    with time_machine.travel(timezone.now() + timedelta(hours=1)):
        ingest(python_source, bumped)

    refreshed = Vacancy.objects.get(pk=original.pk)
    assert Vacancy.objects.count() == 25
    assert refreshed.title == "Renamed"
    assert refreshed.first_seen_at == original.first_seen_at  # still "old" for digests
    assert refreshed.last_seen_at > original.last_seen_at


def test_reappearing_vacancy_is_reactivated(python_source, items):
    ingest(python_source, items)
    Vacancy.objects.update(is_active=False)

    ingest(python_source, items)

    assert Vacancy.objects.filter(is_active=False).count() == 0


def test_vacancy_keeps_its_first_source(python_source, items):
    from radar.models import Source

    other = Source.objects.get(category="JavaScript")
    ingest(python_source, items[:1])
    ingest(other, items[:1])

    assert Vacancy.objects.get().source == python_source


def test_empty_feed_is_noop(python_source):
    assert ingest(python_source, []).created == 0


def test_vacancy_listed_in_several_feeds_keeps_all_categories(python_source, items):
    """Real DOU behaviour: one vacancy can appear in Front End and JavaScript feeds."""
    from radar.models import Source

    javascript = Source.objects.get(category="JavaScript")
    ingest(python_source, items[:3])
    result = ingest(javascript, items[:1])

    assert (result.created, result.updated) == (0, 1)
    both = Vacancy.objects.prefetch_related("sources").get(guid=items[0].guid)
    assert both.source == python_source  # first seen in
    assert both.categories == ["JavaScript", "Python"]
    assert Vacancy.objects.in_categories(["JavaScript"]).count() == 1
    assert Vacancy.objects.in_categories(["Python", "JavaScript"]).count() == 3


def test_repeated_ingest_does_not_duplicate_feed_links(python_source, items):
    ingest(python_source, items)
    ingest(python_source, items)

    assert Vacancy.sources.through.objects.count() == 25
