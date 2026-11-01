from datetime import timedelta
from types import SimpleNamespace

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone

from radar.models import Source
from subscriptions.models import Delivery, Frequency, Subscription
from subscriptions.templatetags.radar_ui import ago, ago_short, places, uk_plural_form, where
from subscriptions.views import TOKEN_COOKIE

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscription():
    return Subscription.objects.create(include_keywords=["python"], categories=["Python"])


@pytest.fixture
def linked(subscription):
    subscription.chat_id = 42
    subscription.linked_at = timezone.now()
    subscription.save()
    return subscription


def url(name, sub=None, query=""):
    args = [sub.token] if sub else []
    return reverse(f"subscriptions:{name}", args=args) + query


def html_of(response) -> str:
    return response.content.decode()


def results_block(html: str) -> str:
    """Only the matches list, without the unfiltered 'Свіжі вакансії' feed."""
    return html.split('id="results"', 1)[1].split('id="feed"', 1)[0]


# --- pages & navigation ------------------------------------------------------------------


def test_home_is_just_the_landing(client):
    html = html_of(client.get("/"))

    assert "Персональний радар вакансій" in html
    assert url("filters") in html and url("bot") in html
    assert "<form" not in html  # filters live on their own page
    assert "Радар, який сам приходить" not in html  # and so does the bot


def test_filters_page_has_form_and_live_preview(client, make_vacancy):
    make_vacancy(title="Fresh Python Role")

    html = html_of(client.get(url("filters")))

    assert "Що шукає твій радар" in html and "Створити радар" in html
    assert "Fresh Python Role" in html


def test_bot_page_without_radar_explains_and_sends_to_filters(client):
    html = html_of(client.get(url("bot")))

    assert "Радар, який сам приходить до тебе" in html
    assert "Як підключити" in html and url("filters") in html
    assert "<form" not in html


@pytest.mark.parametrize(
    ("path", "active"),
    [("/", "Головна"), ("/filters/", "Фільтри"), ("/bot/", "Telegram-бот")],
)
def test_nav_marks_current_page(client, path, active):
    html = html_of(client.get(path))
    nav = html.split('class="site-nav"', 1)[1].split("</nav>", 1)[0]
    assert nav.count('aria-current="page"') == 1
    assert f'aria-current="page">{active}<' in nav


def test_nav_inside_a_radar_stays_in_that_radar(client, subscription):
    html = html_of(client.get(url("radar_bot", subscription)))
    nav = html.split('class="site-nav"', 1)[1].split("</nav>", 1)[0]

    assert f'href="{url("detail", subscription)}"' in nav
    assert f'href="{url("radar_bot", subscription)}" aria-current="page"' in nav


def test_filters_and_bot_redirect_to_remembered_radar(client, subscription):
    client.cookies[TOKEN_COOKIE] = subscription.token

    assert client.get(url("filters"))["Location"] == url("detail", subscription)
    assert client.get(url("bot"))["Location"] == url("radar_bot", subscription)
    assert client.get(url("filters", query="?new=1")).status_code == 200  # start another one


def test_pages_hide_features_that_do_not_exist_yet(client):
    for path in ("/", "/filters/", "/bot/"):
        html = html_of(client.get(path))
        for stub in ("Досвід", "Зарплата від", "Збережені", "Бот підключено", "Зберегти вакансію"):
            assert stub not in html, (path, stub)


def test_filters_preview_is_limited_and_invites_to_create(client, make_vacancy):
    for i in range(12):
        make_vacancy(title=f"Role {i}")

    html = html_of(client.get(url("filters")))

    assert html.count("<article") == 8
    assert "Ще 4 вакансії за цими фільтрами" in html


def test_create_subscription_sets_cookie_timezone_and_redirects(client):
    response = client.post(
        url("filters"),
        {
            "include_keywords": "Python, Django;  django",
            "exclude_keywords": "senior",
            "categories": ["Python", "Front End"],
            "remote_only": "on",
            "timezone": "Europe/Madrid",
        },
    )

    sub = Subscription.objects.get()
    assert response.status_code == 302
    assert response["Location"] == url("detail", sub)
    assert response.cookies[TOKEN_COOKIE].value == sub.token
    assert response.cookies[TOKEN_COOKIE]["httponly"]
    assert sub.include_keywords == ["python", "django"]
    assert sub.exclude_keywords == ["senior"]
    assert sub.categories == ["Front End", "Python"]
    assert sub.remote_only is True
    assert sub.timezone == "Europe/Madrid"


def test_unknown_timezone_from_browser_is_ignored(client):
    client.post(url("filters"), {"timezone": "Mars/Olympus"})
    assert Subscription.objects.get().timezone == "Europe/Kyiv"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"include_keywords": "x" * 401}, "400"),
        ({"include_keywords": ", ".join(f"w{i}" for i in range(16))}, "Не більше 15 слів"),
        ({"exclude_keywords": "a" * 41}, "задовге"),
        ({"categories": ["Cobol"]}, "Cobol"),
    ],
)
def test_invalid_input_is_rejected(client, data, error):
    response = client.post(url("filters"), data)

    assert response.status_code == 200
    assert error in html_of(response)
    assert Subscription.objects.count() == 0


def test_home_links_to_existing_radar(client, subscription):
    client.cookies[TOKEN_COOKIE] = subscription.token
    html = html_of(client.get("/"))
    assert "Відкрити мій радар" in html and url("detail", subscription) in html
    assert url("radar_bot", subscription) in html


# --- radar page ------------------------------------------------------------------------


def test_detail_shows_only_matching_vacancies(client, subscription, make_vacancy):
    make_vacancy(title="Junior Python Developer")
    make_vacancy(title="Java Developer")
    make_vacancy(title="Python in wrong category", categories=["JavaScript"])

    html = html_of(client.get(url("detail", subscription)))
    results = results_block(html)

    assert "Junior Python Developer" in results
    assert "Java Developer" not in results
    assert "Python in wrong category" not in results
    assert '<span id="match-count" class="count-pill mono">1</span>' in html
    assert "Java Developer" in html.split('id="feed"', 1)[1]  # the feed ignores filters


def test_card_shows_matched_keywords_new_badge_and_salary(client, make_vacancy):
    sub = Subscription.objects.create(include_keywords=["django", "python"])
    make_vacancy(
        title="Python dev",
        description_text="Django and DRF",
        salary_text="$800–1200",
        is_remote=True,
        location="Київ, віддалено",
    )

    html = results_block(html_of(client.get(url("detail", sub))))

    assert ">django</span>" in html and ">python</span>" in html
    assert "Нова" in html and "is-new" in html
    assert "$800–1200" in html
    assert "Київ · віддалено" in html


def test_old_vacancy_has_no_new_badge(client, subscription, make_vacancy):
    make_vacancy(title="Python", first_seen_at=timezone.now() - timedelta(days=3))
    assert "is-new" not in results_block(html_of(client.get(url("detail", subscription))))


def test_detail_unknown_token_is_404(client):
    assert client.get("/s/does-not-exist/").status_code == 404


def test_detail_refreshes_cookie(client, subscription):
    response = client.get(url("detail", subscription))
    assert response.cookies[TOKEN_COOKIE].value == subscription.token


def test_update_filters(client, subscription):
    response = client.post(
        url("detail", subscription), {"include_keywords": "django", "categories": ["Python"]}
    )

    subscription.refresh_from_db()
    assert response.status_code == 302
    assert subscription.include_keywords == ["django"]
    assert "Фільтри збережено" in html_of(client.get(response["Location"]))


def test_editing_filters_does_not_change_timezone(client, subscription):
    client.post(url("detail", subscription), {"timezone": "America/New_York"})
    subscription.refresh_from_db()
    assert subscription.timezone == "Europe/Kyiv"


def test_update_with_errors_keeps_old_filters(client, subscription):
    response = client.post(url("detail", subscription), {"include_keywords": "y" * 500})

    subscription.refresh_from_db()
    assert response.status_code == 200
    assert subscription.include_keywords == ["python"]


def test_radar_filters_page_hints_at_bot_until_linked(client, subscription):
    hint = url("radar_bot", subscription)
    assert f'href="{hint}">Підключи бота' in html_of(client.get(url("detail", subscription)))

    subscription.chat_id = 1
    subscription.save()
    assert f'href="{hint}">Підключи бота' not in html_of(client.get(url("detail", subscription)))


def test_radar_bot_page_offers_to_connect(client, subscription):
    html = html_of(client.get(url("radar_bot", subscription)))
    assert url("connect", subscription) in html
    assert "Бот підключено" not in html


def test_radar_bot_page_without_bot_configured(client, subscription, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    html = html_of(client.get(url("radar_bot", subscription)))
    assert url("connect", subscription) not in html
    assert "Бот ще не налаштований" in html


def test_radar_bot_page_unknown_token_is_404(client):
    assert client.get("/s/nope/bot/").status_code == 404


def test_linked_radar_shows_settings_and_next_slot(client, linked, settings):
    linked.frequency = Frequency.EVERY_3H
    linked.save()

    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        html = html_of(client.get(url("radar_bot", linked)))

    assert "Бот підключено" in html
    assert f"@{settings.TELEGRAM_BOT_USERNAME}" in html
    assert "Наступне сповіщення о 18:00" in html and "(Kyiv)" in html
    assert 'value="every_3h" checked' in html
    assert "Відключити" in html


def test_delivered_vacancies_are_marked_seen(client, linked, make_vacancy):
    sent = make_vacancy(title="Python sent")
    make_vacancy(title="Python not sent")
    Delivery.objects.create(subscription=linked, vacancy=sent)

    html = results_block(html_of(client.get(url("detail", linked))))

    assert html.count("is-seen") == 1
    assert "у Telegram" in html


# --- HTMX fragments --------------------------------------------------------------------


def test_results_paginates_with_remaining_count(client, subscription, make_vacancy):
    for i in range(25):
        make_vacancy(title=f"Python role {i}")

    first = html_of(client.get(url("results", subscription)))
    more = html_of(client.get(url("results", subscription, "?offset=20")))

    assert first.count("<article") == 20
    assert "Показати ще 5" in first
    assert more.count("<article") == 5
    assert 'id="results"' not in more and "Показати ще" not in more


def test_results_reload_resets_live_state_out_of_band(client, subscription, make_vacancy):
    v = make_vacancy(title="Python")

    html = html_of(client.get(url("results", subscription)))

    assert f"?since={v.pk}" in html
    assert 'id="new-tab"' in html and "Нові ·" not in html
    assert 'id="match-count" class="count-pill mono" hx-swap-oob="true">1<' in html


def test_preview_shows_unsaved_filters_without_saving(client, subscription, make_vacancy):
    make_vacancy(title="Django role")
    make_vacancy(title="Python role")

    html = html_of(
        client.get(url("preview", query=f"?include_keywords=django&token={subscription.token}"))
    )

    assert "Django role" in html and "Python role" not in html
    assert "попередній перегляд" in html
    subscription.refresh_from_db()
    assert subscription.include_keywords == ["python"]


def test_preview_with_saved_filters_has_no_unsaved_note(client, subscription):
    query = f"?include_keywords=python&categories=Python&token={subscription.token}"
    assert "попередній перегляд" not in html_of(client.get(url("preview", query=query)))


def test_preview_reports_errors(client):
    html = html_of(client.get(url("preview", query="?include_keywords=" + "z" * 500)))
    assert "Перевір фільтри" in html


def test_live_counts_new_matches_since_cursor(client, subscription, make_vacancy):
    old = make_vacancy(title="Python old")
    live_url = url("live", subscription, f"?since={old.pk}")

    assert "Нові ·" not in html_of(client.get(live_url))

    make_vacancy(title="Python new 1")
    make_vacancy(title="Python new 2")
    make_vacancy(title="Java new")  # does not match
    html = html_of(client.get(live_url))

    assert "Нові · 2" in html
    assert f"?since={old.pk}" in html  # keeps the same cursor until the list reloads


def test_live_stamp_uses_last_fetch_time_not_poll_time(client, subscription):
    Source.objects.filter(category="Python").update(
        last_fetched_at=timezone.now() - timedelta(minutes=14)
    )
    assert "оновлено 14 хв тому" in html_of(client.get(url("live", subscription)))


def test_live_stamp_before_any_fetch(client, subscription):
    assert "ще не оновлювалось" in html_of(client.get(url("live", subscription)))


def test_toast_pops_up_when_a_fetch_brings_new_matches(client, subscription, make_vacancy):
    old = make_vacancy(title="Python old")
    live_url = url("live", subscription, f"?since={old.pk}&shown=0")
    assert "data-toast" not in html_of(client.get(live_url))  # nothing new yet

    make_vacancy(title="Python brand new")
    html = html_of(client.get(live_url))

    assert "data-toast" in html and "1 нова вакансія" in html and "Python brand new" in html
    assert f"since={old.pk}&amp;shown=1" in html  # the poller now remembers it was announced


def test_toast_does_not_repeat_for_the_same_count(client, subscription, make_vacancy):
    old = make_vacancy(title="Python old")
    make_vacancy(title="Python new")

    html = html_of(client.get(url("live", subscription, f"?since={old.pk}&shown=1")))

    assert "data-toast" not in html
    assert "Нові · 1" in html  # the tab still shows it


def test_toast_comes_back_when_more_arrive(client, subscription, make_vacancy):
    old = make_vacancy(title="Python old")
    make_vacancy(title="Python new 1")
    make_vacancy(title="Python new 2")

    html = html_of(client.get(url("live", subscription, f"?since={old.pk}&shown=1")))

    assert "2 нові вакансії" in html and "shown=2" in html


def test_toast_can_be_switched_off(client, subscription, make_vacancy, settings):
    settings.NEW_VACANCIES_TOAST = False
    old = make_vacancy(title="Python old")
    make_vacancy(title="Python new")
    assert "data-toast" not in html_of(client.get(url("live", subscription, f"?since={old.pk}")))


def test_reloading_the_list_clears_the_toast_and_resets_the_counter(client, subscription):
    html = html_of(client.get(url("results", subscription)))
    assert '<div id="toast-slot" hx-swap-oob="true"></div>' in html
    assert "shown=0" in html


def test_live_ignores_garbage_cursor(client, subscription):
    assert client.get(url("live", subscription, "?since=abc")).status_code == 200


def test_feed_partial(client, make_vacancy):
    make_vacancy(title="Anything new")
    html = html_of(client.get(url("feed")))
    assert "Anything new" in html and 'id="feed"' in html


# --- connect (step 2) ---------------------------------------------------------------------


def test_connect_page_has_deep_link_copy_and_qr(client, subscription, settings):
    html = html_of(client.get(url("connect", subscription)))

    deep_link = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={subscription.token}"
    assert f'href="{deep_link}"' in html
    assert f'data-copy="{deep_link}"' in html
    assert "<svg" in html.split('class="qr"', 1)[1]
    assert url("link_status", subscription) in html


def test_connect_without_bot_configured(client, subscription, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    html = html_of(client.get(url("connect", subscription)))
    assert "?start=" not in html and "не налаштований" in html


def test_connect_when_already_linked_redirects(client, linked):
    response = client.get(url("connect", linked))
    assert response.status_code == 302 and response["Location"] == url("radar_bot", linked)


def test_link_status_waits_then_redirects_to_frequency(client, subscription):
    waiting = client.get(url("link_status", subscription))
    assert waiting.status_code == 200 and "Чекаємо" in html_of(waiting)

    subscription.chat_id = 7
    subscription.save()
    linked = client.get(url("link_status", subscription))
    assert linked["HX-Redirect"] == url("frequency", subscription)


# --- frequency (step 3) & bot settings ------------------------------------------------------


def test_frequency_page(client, linked, make_vacancy):
    Delivery.objects.create(subscription=linked, vacancy=make_vacancy(title="Python"))

    html = html_of(client.get(url("frequency", linked)))

    assert "Як часто надсилати нове?" in html
    assert "Бот підключено" in html and ">1</span> вакансій" in html
    for label in (
        "Щогодини",
        "Кожні 3 год",
        "Кожні 6 год",
        "Кожні 12 год",
        "Раз на день",
        "Вимкнено",
    ):
        assert label in html
    assert "Радимо" in html


def test_frequency_save_redirects_with_next_slot(client, linked):
    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        response = client.post(
            url("frequency", linked),
            {"frequency": "every_6h", "quiet_hours_start": "23", "quiet_hours_end": "7"},
        )
        page = html_of(client.get(response["Location"]))

    linked.refresh_from_db()
    assert response["Location"] == url("detail", linked)  # "Зберегти і перейти до збігів"
    assert (linked.frequency, linked.quiet_hours_enabled) == ("every_6h", False)
    assert (linked.quiet_hours_start, linked.quiet_hours_end) == (23, 7)
    assert "Наступне сповіщення о 21:00" in page


def test_frequency_skip_turns_notifications_off(client, linked):
    client.post(url("frequency", linked), {"skip": "1"})
    linked.refresh_from_db()
    assert linked.frequency == Frequency.OFF


def test_frequency_rejects_unknown_values(client, linked):
    response = client.post(
        url("frequency", linked),
        {"frequency": "every_minute", "quiet_hours_start": "22", "quiet_hours_end": "8"},
    )
    linked.refresh_from_db()
    assert response.status_code == 200
    assert linked.frequency == Frequency.DAILY


def test_notifications_card_autosaves(client, linked):
    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        response = client.post(
            url("notifications", linked),
            {
                "frequency": "hourly",
                "quiet_hours_enabled": "on",
                "quiet_hours_start": "22",
                "quiet_hours_end": "8",
                "timezone": "Europe/Madrid",
            },
        )
    linked.refresh_from_db()
    html = html_of(response)
    assert linked.frequency == "hourly" and linked.timezone == "Europe/Madrid"
    assert 'id="bot-card"' in html and "Збережено" in html
    assert "Наступне сповіщення о 16:00" in html and "(Madrid)" in html  # 15:40 in Madrid


def test_notifications_card_saves_only_the_changed_field(client, linked):
    linked.frequency = Frequency.HOURLY  # set elsewhere (the bot) after the page loaded
    linked.save()

    client.post(
        url("notifications", linked),
        {
            "changed": "quiet_hours_start",
            "frequency": "daily",  # stale
            "quiet_hours_enabled": "on",
            "quiet_hours_start": "23",
            "quiet_hours_end": "8",
        },
    )

    linked.refresh_from_db()
    assert (linked.frequency, linked.quiet_hours_start) == (Frequency.HOURLY, 23)


def test_notifications_card_get_rereads_database(client, linked):
    linked.frequency = Frequency.EVERY_6H
    linked.save()
    html = html_of(client.get(url("notifications", linked)))
    assert 'id="bot-card"' in html and 'value="every_6h" checked' in html


def test_notifications_card_ignores_unknown_changed_field(client, linked):
    client.post(url("notifications", linked), {"changed": "chat_id", "chat_id": "1"})
    linked.refresh_from_db()
    assert linked.chat_id == 42


def test_next_slot_preview_does_not_save(client, linked):
    with time_machine.travel("2026-11-02 16:40 +02:00", tick=False):
        html = html_of(
            client.get(
                url(
                    "next_slot",
                    linked,
                    "?frequency=every_12h&quiet_hours_start=22&quiet_hours_end=8",
                )
            )
        )
    linked.refresh_from_db()
    assert "о 21:00" in html
    assert linked.frequency == Frequency.DAILY


def test_unlink(client, linked):
    response = client.post(url("unlink", linked))
    linked.refresh_from_db()
    assert response["Location"] == url("radar_bot", linked)
    assert linked.chat_id is None and linked.linked_at is None


# --- template helpers ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "a"), (2, "b"), (4, "b"), (5, "c"), (11, "c"), (12, "c"), (21, "a"), (22, "b"), (0, "c")],
)
def test_uk_plural(n, expected):
    assert uk_plural_form(n, "a", "b", "c") == expected


def test_ago_and_ago_short():
    now = timezone.now()
    assert ago(now, now) == "щойно"
    assert ago(now - timedelta(minutes=5), now) == "5 хв тому"
    assert ago(now - timedelta(hours=3), now) == "3 год тому"
    assert ago(now - timedelta(days=1, hours=2), now) == "вчора"
    assert ago(now - timedelta(days=5), now) == "5 днів тому"
    assert ago_short(now - timedelta(seconds=20), now) == "1 хв"
    assert ago_short(now - timedelta(hours=2), now) == "2 год"


def test_places_and_where():
    assert places("Київ, віддалено") == "Київ"
    assert where(SimpleNamespace(location="Київ, Львів, віддалено", is_remote=True)) == (
        "Київ, Львів · віддалено"
    )
    assert where(SimpleNamespace(location="віддалено", is_remote=True)) == "Віддалено"
    assert where(SimpleNamespace(location="Київ", is_remote=False)) == "Київ"
