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
