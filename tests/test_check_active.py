from datetime import timedelta

import httpx
import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from bot.models import ProcessedUpdate
from radar.matching import Filters
from radar.models import Vacancy
from radar.search import search
from radar.services import activity
from radar.services.activity import Status, check_active, check_vacancy, page_says_inactive

pytestmark = pytest.mark.django_db


@pytest.fixture
def pages(load_fixture):
    return {
        "active": load_fixture("dou_vacancy_active.html").decode(),
        "inactive": load_fixture("dou_vacancy_inactive.html").decode(),
    }


@pytest.fixture
def old(make_vacancy):
    """A vacancy that dropped out of the RSS feed two days ago."""

    def _make(**kwargs):
        kwargs.setdefault("last_seen_at", timezone.now() - timedelta(days=2))
        return make_vacancy(**kwargs)

    return _make


def client_for(responses: dict):
    """url -> httpx.Response | Exception; records requested urls."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        item = responses[url]
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen
    return client


def no_sleep(seconds):
    pass


# --- reading a page ------------------------------------------------------------------------


def test_inactive_marker_is_read_from_the_heading(pages):
    assert page_says_inactive(pages["inactive"]) is True
    assert page_says_inactive(pages["active"]) is False  # phrase only in the description
    assert page_says_inactive("<html><body>no heading</body></html>") is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(404), Status.INACTIVE),
        (httpx.Response(410), Status.INACTIVE),
        (httpx.Response(403), Status.UNKNOWN),
        (httpx.ConnectError("dns"), Status.UNKNOWN),
    ],
)
def test_check_vacancy_status_codes(response, expected):
    url = "https://jobs.dou.ua/companies/a/vacancies/1/"
    assert check_vacancy(client_for({url: response}), url, sleep=no_sleep) is expected


def test_check_vacancy_reads_page(pages):
    url = "https://jobs.dou.ua/companies/a/vacancies/1/"
    open_page = client_for({url: httpx.Response(200, text=pages["active"])})
    closed_page = client_for({url: httpx.Response(200, text=pages["inactive"])})
    assert check_vacancy(open_page, url, sleep=no_sleep) is Status.ACTIVE
    assert check_vacancy(closed_page, url, sleep=no_sleep) is Status.INACTIVE


def test_server_errors_are_retried_then_unknown():
    url = "https://jobs.dou.ua/companies/a/vacancies/1/"
    client = client_for({url: httpx.Response(503)})
    assert check_vacancy(client, url, sleep=no_sleep) is Status.UNKNOWN
    assert len(client.seen) == 4  # 1 + FETCH_MAX_RETRIES


# --- a run ----------------------------------------------------------------------------------


def test_run_marks_closed_and_keeps_open(old, pages):
    gone = old(title="Gone")
    closed = old(title="Closed")
    open_ = old(title="Open")
    flaky = old(title="Flaky")
    client = client_for(
        {
            gone.url: httpx.Response(404),
            closed.url: httpx.Response(200, text=pages["inactive"]),
            open_.url: httpx.Response(200, text=pages["active"]),
            flaky.url: httpx.Response(403),
        }
    )

    run = check_active(client=client, sleep=no_sleep)

    assert (run.checked, run.closed, run.still_active, run.unknown) == (4, 2, 1, 1)
    states = dict(Vacancy.objects.values_list("title", "is_active"))
    assert states == {"Gone": False, "Closed": False, "Open": True, "Flaky": True}
    assert not Vacancy.objects.filter(last_checked_at__isnull=True).exists()


def test_vacancies_still_in_the_feed_are_not_opened(make_vacancy):
    make_vacancy(title="In feed")  # last_seen_at = now
    client = client_for({})
    assert check_active(client=client, sleep=no_sleep).checked == 0
    assert client.seen == []


def test_recently_checked_and_closed_ones_are_skipped(old):
    old(last_checked_at=timezone.now() - timedelta(hours=2))
    old(is_active=False)
    assert check_active(client=client_for({}), sleep=no_sleep).checked == 0


def test_oldest_checked_first_and_limited(old, pages):
    never = old(title="Never checked")
    long_ago = old(title="Checked long ago", last_checked_at=timezone.now() - timedelta(days=5))
    old(title="Checked yesterday", last_checked_at=timezone.now() - timedelta(days=1))
    page = httpx.Response(200, text=pages["active"])
    client = client_for({never.url: page, long_ago.url: page})

    check_active(limit=2, client=client, sleep=no_sleep)

    assert client.seen == [never.url, long_ago.url]


def test_pauses_between_requests(old, pages, settings):
    settings.CHECK_ACTIVE_DELAY_SECONDS = 1.5
    a, b, c = old(), old(), old()
    page = httpx.Response(200, text=pages["active"])
    sleeps = []

    check_active(client=client_for({v.url: page for v in (a, b, c)}), sleep=sleeps.append)

    assert sleeps == [1.5, 1.5]  # between requests, not before the first


def test_closed_vacancy_disappears_from_matches_and_reappears_from_feed(old, pages):
    from radar.services.fetcher import ingest
    from radar.services.parser import ParsedVacancy

    vacancy = old(title="Python dev")
    check_active(client=client_for({vacancy.url: httpx.Response(404)}), sleep=no_sleep)
    assert search(Filters(include=("python",))).total == 0

    # Back in the feed (re-posted): active again.
    item = ParsedVacancy(
        guid=vacancy.guid,
        url=vacancy.url,
        title="Python dev",
        company="Acme",
        location="",
        is_remote=False,
        salary_text="",
        description_text="",
        published_at=timezone.now(),
    )
    ingest(vacancy.source, [item])
    assert search(Filters(include=("python",))).total == 1


# --- command ----------------------------------------------------------------------------------


def test_command_summary_and_housekeeping(old, monkeypatch, capsys):
    vacancy = old()
    monkeypatch.setattr(
        activity, "build_client", lambda: client_for({vacancy.url: httpx.Response(404)})
    )
    ProcessedUpdate.objects.create(update_id=1)
    ProcessedUpdate.objects.filter(update_id=1).update(
        processed_at=timezone.now() - timedelta(days=8)
    )
    ProcessedUpdate.objects.create(update_id=2)

    call_command("check_active")

    out = capsys.readouterr().out
    assert "Checked 1: 1 closed" in out and "Pruned 1" in out
    assert list(ProcessedUpdate.objects.values_list("update_id", flat=True)) == [2]


def test_command_fails_when_nothing_could_be_checked(old, monkeypatch):
    vacancy = old()
    monkeypatch.setattr(
        activity, "build_client", lambda: client_for({vacancy.url: httpx.Response(403)})
    )
    with pytest.raises(CommandError, match="DOU reachable"):
        call_command("check_active")


def test_command_skips_when_already_running(capsys):
    from radar.locks import acquire

    acquire("check_active", timedelta(hours=2))
    call_command("check_active")
    assert "already running" in capsys.readouterr().out
