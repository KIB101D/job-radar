from datetime import timedelta

import httpx
import pytest
from django.core.management import CommandError, call_command

from radar.locks import FETCH_LOCK, acquire, job_lock, release
from radar.models import Source, Vacancy
from radar.services import fetcher
from radar.services.fetcher import FetchError, fetch_all_sources, fetch_source, fetch_url

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/?category=Python"


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def sequence(*responses):
    """Handler returning the given responses (or raising exceptions) in order."""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    handler.calls = calls
    return handler


# --- fetch_url: retry policy ---------------------------------------------------


def test_retries_5xx_with_exponential_backoff(no_sleep):
    handler = sequence(httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok"))

    response = fetch_url(make_client(handler), FEED_URL, max_retries=3, sleep=no_sleep)

    assert response.text == "ok"
    assert len(handler.calls) == 3
    assert no_sleep.calls == [1.0, 2.0]


def test_respects_retry_after_on_429(no_sleep):
    handler = sequence(
        httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")
    )

    fetch_url(make_client(handler), FEED_URL, max_retries=3, sleep=no_sleep)

    assert no_sleep.calls == [7.0]


def test_retries_timeouts(no_sleep):
    handler = sequence(httpx.ReadTimeout("slow"), httpx.Response(200, text="ok"))

    assert fetch_url(make_client(handler), FEED_URL, sleep=no_sleep).text == "ok"
    assert len(handler.calls) == 2


def test_does_not_retry_404(no_sleep):
    handler = sequence(httpx.Response(404))

    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_url(make_client(handler), FEED_URL, max_retries=3, sleep=no_sleep)

    assert len(handler.calls) == 1
    assert no_sleep.calls == []


def test_does_not_retry_connection_errors(no_sleep):
    handler = sequence(httpx.ConnectError("dns"))

    with pytest.raises(FetchError, match="ConnectError"):
        fetch_url(make_client(handler), FEED_URL, sleep=no_sleep)
    assert len(handler.calls) == 1


def test_gives_up_after_max_retries(no_sleep):
    handler = sequence(httpx.Response(500))

    with pytest.raises(FetchError, match="HTTP 500 after 3 attempts"):
        fetch_url(make_client(handler), FEED_URL, max_retries=2, sleep=no_sleep)
    assert len(handler.calls) == 3


def test_sends_custom_user_agent(settings):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["User-Agent"]
        return httpx.Response(200)

    fetch_url(fetcher.build_client(transport=httpx.MockTransport(handler)), FEED_URL)

    assert seen["ua"] == settings.FETCH_USER_AGENT


# --- fetch_source / fetch_all_sources ------------------------------------------


@pytest.mark.django_db
def test_fetch_source_saves_vacancies_and_marks_success(python_source, load_fixture, no_sleep):
    python_source.last_error = "old error"
    python_source.save()
    client = make_client(sequence(httpx.Response(200, content=load_fixture("dou_python.xml"))))

    result = fetch_source(python_source, client, sleep=no_sleep)

    python_source.refresh_from_db()
    assert result.ok and result.created == 25
    assert Vacancy.objects.count() == 25
    assert python_source.last_fetched_at is not None
    assert python_source.last_error == ""


@pytest.mark.django_db
def test_fetch_source_records_error_and_keeps_last_success(python_source, no_sleep):
    client = make_client(sequence(httpx.Response(200, text="<html>challenge</html>")))

    result = fetch_source(python_source, client, sleep=no_sleep)

    python_source.refresh_from_db()
    assert not result.ok
    assert "FeedParseError" in python_source.last_error
    assert python_source.last_fetched_at is None  # only set on success


@pytest.mark.django_db
def test_one_broken_source_does_not_stop_others(load_fixture, no_sleep):
    feed = load_fixture("dou_python.xml")

    def handler(request):
        if "JavaScript" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=feed)

    results = fetch_all_sources(client=make_client(handler), sleep=no_sleep)

    assert len(results) == 4  # seeded sources
    assert [r.ok for r in results].count(False) == 1
    assert Vacancy.objects.count() == 25


@pytest.mark.django_db
def test_sources_are_fetched_sequentially_with_pause(settings, load_fixture, no_sleep):
    settings.FETCH_DELAY_BETWEEN_SOURCES_SECONDS = 2
    client = make_client(sequence(httpx.Response(200, content=load_fixture("dou_python.xml"))))

    fetch_all_sources(client=client, sleep=no_sleep)

    assert no_sleep.calls == [2, 2, 2]  # 4 sources -> 3 pauses


@pytest.mark.django_db
def test_disabled_sources_are_skipped(load_fixture, no_sleep):
    Source.objects.exclude(category="Python").update(is_enabled=False)
    handler = sequence(httpx.Response(200, content=load_fixture("dou_python.xml")))

    results = fetch_all_sources(client=make_client(handler), sleep=no_sleep)

    assert [r.source.category for r in results] == ["Python"]


# --- lock ----------------------------------------------------------------------


@pytest.mark.django_db
def test_lock_is_exclusive_until_released():
    assert acquire("job", timedelta(minutes=5)) is True
    assert acquire("job", timedelta(minutes=5)) is False
    release("job")
    assert acquire("job", timedelta(minutes=5)) is True


@pytest.mark.django_db
def test_lock_expires_after_ttl():
    import time_machine
    from django.utils import timezone

    assert acquire("job", timedelta(minutes=5))
    with time_machine.travel(timezone.now() + timedelta(minutes=6)):
        assert acquire("job", timedelta(minutes=5)) is True


@pytest.mark.django_db
def test_job_lock_context_manager_releases_on_error():
    with pytest.raises(RuntimeError), job_lock("job", timedelta(minutes=5)) as acquired:
        assert acquired
        raise RuntimeError
    assert acquire("job", timedelta(minutes=5)) is True


# --- management command --------------------------------------------------------


@pytest.fixture
def mock_http(monkeypatch, load_fixture):
    feed = load_fixture("dou_python.xml")
    state = {"status": 200}

    def handler(request):
        return httpx.Response(state["status"], content=feed)

    real_build_client = fetcher.build_client
    monkeypatch.setattr(
        fetcher,
        "build_client",
        lambda: real_build_client(transport=httpx.MockTransport(handler)),
    )
    return state


@pytest.mark.django_db
def test_command_fetches_and_reports(mock_http, capsys):
    call_command("fetch_vacancies", category=["Python"])

    out = capsys.readouterr().out
    assert "DOU: Python: ok, 25 new" in out
    assert Vacancy.objects.count() == 25


@pytest.mark.django_db
def test_command_skips_when_lock_is_held(mock_http, capsys):
    acquire(FETCH_LOCK, timedelta(minutes=10))

    call_command("fetch_vacancies")

    assert "already running" in capsys.readouterr().out
    assert Vacancy.objects.count() == 0


@pytest.mark.django_db
def test_command_fails_only_when_all_sources_fail(mock_http, settings):
    settings.FETCH_MAX_RETRIES = 0
    mock_http["status"] = 404

    with pytest.raises(CommandError, match="All sources failed"):
        call_command("fetch_vacancies")
