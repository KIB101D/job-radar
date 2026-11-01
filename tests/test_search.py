from datetime import timedelta

import pytest

from radar.matching import Filters
from radar.search import fresh_feed, latest_vacancy_id, newer_than, search

pytestmark = pytest.mark.django_db


def test_search_combines_db_filters_and_keywords(make_vacancy):
    hit = make_vacancy(title="Junior Python Developer", is_remote=True)
    make_vacancy(title="Junior Python Developer", is_remote=False)  # not remote
    make_vacancy(title="Junior Java Developer", is_remote=True)  # keyword
    make_vacancy(title="Junior Python", is_remote=True, categories=["JavaScript"])  # category
    make_vacancy(title="Junior Python", is_remote=True, is_active=False)  # closed

    f = Filters(include=("python",), categories=frozenset({"Python"}), remote_only=True)
    page = search(f)

    assert [v.pk for v in page.vacancies] == [hit.pk]
    assert page.has_more is False


def test_search_vacancy_in_two_categories_is_returned_once(make_vacancy):
    v = make_vacancy(categories=["Front End", "JavaScript"])

    page = search(Filters(categories=frozenset({"Front End", "JavaScript"})))

    assert [x.pk for x in page.vacancies] == [v.pk]


def test_search_pagination(make_vacancy):
    for _ in range(5):
        make_vacancy()

    first = search(Filters(), limit=2)
    second = search(Filters(), offset=first.next_offset, limit=2)
    third = search(Filters(), offset=second.next_offset, limit=2)

    assert first.has_more and second.has_more and not third.has_more
    assert first.total == third.total == 5
    ids = [v.pk for page in (first, second, third) for v in page.vacancies]
    assert len(ids) == len(set(ids)) == 5


def test_search_orders_newest_first(make_vacancy):
    older = make_vacancy()
    newer = make_vacancy(published_at=older.published_at + timedelta(hours=1))

    assert [v.pk for v in search(Filters()).vacancies] == [newer.pk, older.pk]


def test_newer_than_counts_only_new_matching(make_vacancy):
    make_vacancy(title="Python dev")
    cursor = latest_vacancy_id()
    new_hit = make_vacancy(title="Python dev")
    make_vacancy(title="Java dev")

    assert [v.pk for v in newer_than(Filters(include=("python",)), cursor)] == [new_hit.pk]


def test_latest_vacancy_id_on_empty_db():
    assert latest_vacancy_id() == 0


def test_fresh_feed_ignores_filters_and_is_limited(make_vacancy):
    for _ in range(12):
        make_vacancy()
    make_vacancy(is_active=False)

    assert len(fresh_feed(limit=10)) == 10
    assert all(v.is_active for v in fresh_feed(limit=20))


def test_remote_or_city_is_not_narrowed_to_remote_in_sql(make_vacancy):
    make_vacancy(title="Kyiv office", location="Київ")
    make_vacancy(title="Remote", location="віддалено", is_remote=True)
    make_vacancy(title="Kharkiv", location="Харків")

    page = search(Filters(remote_only=True, cities=frozenset({"Київ"})))
    assert sorted(v.title for v in page.vacancies) == ["Kyiv office", "Remote"]


def test_popular_cities_most_common_first_without_remote(make_vacancy):
    from radar.search import popular_cities

    make_vacancy(title="A", location="Київ, Львів, віддалено")
    make_vacancy(title="B", location="Львів")
    make_vacancy(title="C", location="Одеса", is_active=False)  # closed: not counted
    assert popular_cities() == ["Львів", "Київ"]
