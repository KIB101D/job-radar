import pytest
from django.urls import reverse

from subscriptions.models import Delivery, Subscription
from subscriptions.templatetags.radar_ui import ago, places, uk_plural_form
from subscriptions.views import TOKEN_COOKIE

pytestmark = pytest.mark.django_db


@pytest.fixture
def subscription():
    return Subscription.objects.create(include_keywords=["python"], categories=["Python"])


def detail_url(sub, name="detail"):
    return reverse(f"subscriptions:{name}", args=[sub.token])


# --- home / create ---------------------------------------------------------------


def test_home_shows_form_and_fresh_feed(client, make_vacancy):
    make_vacancy(title="Fresh Python Role")

    response = client.get("/")

    assert response.status_code == 200
    html = response.content.decode()
    assert "Створити радар" in html
    assert "Свіжі вакансії" in html
    assert "Fresh Python Role" in html


def test_create_subscription_sets_cookie_and_redirects(client):
    response = client.post(
        "/",
        {
            "include_keywords": "Python, Django;  django",
            "exclude_keywords": "senior",
            "categories": ["Python", "Front End"],
            "remote_only": "on",
        },
    )

    sub = Subscription.objects.get()
    assert response.status_code == 302
    assert response["Location"] == detail_url(sub)
    assert response.cookies[TOKEN_COOKIE].value == sub.token
    assert response.cookies[TOKEN_COOKIE]["httponly"]
    assert sub.include_keywords == ["python", "django"]
    assert sub.exclude_keywords == ["senior"]
    assert sub.categories == ["Front End", "Python"]
    assert sub.remote_only is True


def test_empty_form_is_valid_and_means_everything(client):
    client.post("/", {})
    sub = Subscription.objects.get()
    assert (sub.include_keywords, sub.categories, sub.remote_only) == ([], [], False)


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
    response = client.post("/", data)

    assert response.status_code == 200
    assert error in response.content.decode()
    assert Subscription.objects.count() == 0


def test_home_links_back_to_existing_radar(client, subscription):
    client.cookies[TOKEN_COOKIE] = subscription.token
    assert detail_url(subscription) in client.get("/").content.decode()


# --- subscription page ---------------------------------------------------------------


def results_block(html: str) -> str:
    """Just the results list, without the unfiltered 'Свіжі вакансії' feed."""
    return html.split('id="results"', 1)[1].split('id="feed"', 1)[0]


def test_detail_shows_only_matching_vacancies(client, subscription, make_vacancy):
    make_vacancy(title="Junior Python Developer")
    make_vacancy(title="Java Developer")
    make_vacancy(title="Python in wrong category", categories=["JavaScript"])

    html = client.get(detail_url(subscription)).content.decode()
    results = results_block(html)

    assert "Junior Python Developer" in results
    assert "Java Developer" not in results
    assert "Python in wrong category" not in results
    assert "На радарі 1 вакансія" in html
    assert "Java Developer" in html.split('id="feed"', 1)[1]  # the feed ignores filters


def test_detail_unknown_token_is_404(client):
    assert client.get("/s/does-not-exist/").status_code == 404


def test_detail_refreshes_cookie(client, subscription):
    response = client.get(detail_url(subscription))
    assert response.cookies[TOKEN_COOKIE].value == subscription.token


def test_update_filters(client, subscription):
    response = client.post(
        detail_url(subscription), {"include_keywords": "django", "categories": ["Python"]}
    )

    subscription.refresh_from_db()
    assert response.status_code == 302
    assert subscription.include_keywords == ["django"]
    assert "Фільтри збережено" in client.get(response["Location"]).content.decode()


def test_update_with_errors_keeps_old_filters(client, subscription):
    response = client.post(detail_url(subscription), {"include_keywords": "y" * 500})

    subscription.refresh_from_db()
    assert response.status_code == 200
    assert subscription.include_keywords == ["python"]


def test_telegram_deep_link(client, subscription, settings):
    html = client.get(detail_url(subscription)).content.decode()
    assert f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={subscription.token}" in html


def test_telegram_button_disabled_without_bot(client, subscription, settings):
    settings.TELEGRAM_BOT_USERNAME = ""
    html = client.get(detail_url(subscription)).content.decode()
    assert "?start=" not in html
    assert "Бот ще не налаштований" in html


def test_linked_chat_shows_state_instead_of_deep_link(client, subscription):
    subscription.chat_id = 42
    subscription.save()

    html = client.get(detail_url(subscription)).content.decode()

    assert "Telegram підключено" in html
    assert "?start=" not in html


def test_delivered_vacancies_are_marked_seen(client, subscription, make_vacancy):
    sent = make_vacancy(title="Python sent")
    make_vacancy(title="Python not sent")
    subscription.chat_id = 42
    subscription.save()
    Delivery.objects.create(subscription=subscription, vacancy=sent)

    html = client.get(detail_url(subscription)).content.decode()

    assert html.count("is-seen") == 1
    assert "Надіслано в Telegram" in html


# --- HTMX partials ------------------------------------------------------------------


def test_results_partial_paginates(client, subscription, make_vacancy):
    for i in range(25):
        make_vacancy(title=f"Python role {i}")

    first = client.get(detail_url(subscription, "results")).content.decode()
    more = client.get(detail_url(subscription, "results") + "?offset=20").content.decode()

    assert first.count("<article") == 20
    assert 'id="results"' in first and "offset=20" in first
    assert more.count("<article") == 5
    assert 'id="results"' not in more and "Показати ще" not in more


def test_results_first_page_resets_banner_out_of_band(client, subscription, make_vacancy):
    v = make_vacancy(title="Python")

    html = client.get(detail_url(subscription, "results")).content.decode()

    assert 'hx-swap-oob="true"' in html
    assert f"since={v.pk}" in html


def test_new_count_banner(client, subscription, make_vacancy):
    old = make_vacancy(title="Python old")
    url = detail_url(subscription, "new_count") + f"?since={old.pk}"

    assert "показати" not in client.get(url).content.decode()

    make_vacancy(title="Python new 1")
    make_vacancy(title="Python new 2")
    make_vacancy(title="Java new")  # does not match
    html = client.get(url).content.decode()

    assert "2 нові вакансії, показати" in html
    assert f"since={old.pk}" in html  # keeps polling from the same cursor


def test_new_count_ignores_garbage_cursor(client, subscription):
    assert client.get(detail_url(subscription, "new_count") + "?since=abc").status_code == 200


def test_feed_partial(client, make_vacancy):
    make_vacancy(title="Anything new")
    html = client.get(reverse("subscriptions:feed")).content.decode()
    assert "Anything new" in html and 'id="feed"' in html


# --- template helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "a"), (2, "b"), (4, "b"), (5, "c"), (11, "c"), (12, "c"), (21, "a"), (22, "b"), (0, "c")],
)
def test_uk_plural(n, expected):
    assert uk_plural_form(n, "a", "b", "c") == expected


def test_ago():
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    assert ago(now, now) == "щойно"
    assert ago(now - timedelta(minutes=5), now) == "5 хв тому"
    assert ago(now - timedelta(hours=3), now) == "3 год тому"
    assert ago(now - timedelta(days=1, hours=2), now) == "вчора"
    assert ago(now - timedelta(days=5), now) == "5 днів тому"


def test_places_drops_remote():
    assert places("Київ, віддалено") == "Київ"
    assert places("віддалено") == ""
