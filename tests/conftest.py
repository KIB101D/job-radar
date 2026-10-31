from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _load


@pytest.fixture
def python_source(db):
    from radar.models import Source

    # Seeded by the data migration.
    return Source.objects.get(category="Python")


@pytest.fixture
def no_sleep():
    """A sleep() replacement that records delays instead of waiting."""
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    _sleep.calls = calls
    return _sleep


@pytest.fixture
def make_vacancy(db):
    """Create a Vacancy with sensible defaults; `categories` attaches feeds."""
    from datetime import timedelta

    from django.utils import timezone

    from radar.models import Source, Vacancy

    counter = {"n": 0}

    def _make(categories=("Python",), **kwargs):
        counter["n"] += 1
        n = counter["n"]
        now = timezone.now()
        sources = [Source.objects.get(category=c) for c in categories]
        defaults = {
            "guid": f"https://jobs.dou.ua/companies/acme/vacancies/{900000 + n}/",
            "url": f"https://jobs.dou.ua/companies/acme/vacancies/{900000 + n}/",
            "title": f"Vacancy {n}",
            "company": "Acme",
            "description_text": "",
            "location": "Київ",
            "published_at": now - timedelta(minutes=n),
            "first_seen_at": now,
            "last_seen_at": now,
            "source": sources[0],
        }
        defaults.update(kwargs)
        vacancy = Vacancy.objects.create(**defaults)
        vacancy.sources.set(sources)
        return vacancy

    return _make
