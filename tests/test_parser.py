from datetime import UTC, datetime

import pytest

from radar.services.parser import (
    FeedParseError,
    canonical_url,
    clean_text,
    detect_remote,
    html_to_text,
    parse_feed,
    split_title,
)

# --- real DOU feed -------------------------------------------------------------


@pytest.fixture
def dou_items(load_fixture):
    return parse_feed(load_fixture("dou_python.xml"))


def by_id(items, vacancy_id: str):
    return next(v for v in items if f"/vacancies/{vacancy_id}/" in v.guid)


def test_parses_all_items_from_real_feed(dou_items):
    assert len(dou_items) == 25


def test_title_company_salary_location(dou_items):
    v = by_id(dou_items, "378476")
    assert v.title == "Senior Backend Developer / AI Engineer (Python, Remote)"
    assert v.company == "7perpendiculars"
    assert v.salary_text == "$4000–7000"
    assert v.location == "віддалено"
    assert v.is_remote is True


def test_company_with_parentheses_and_several_locations(dou_items):
    v = by_id(dou_items, "367416")
    assert v.company == "ПУМБ (First Ukrainian International Bank)"
    assert v.salary_text == "$3500–4500"
    assert v.location == "за кордоном, віддалено"


def test_locations_with_parentheses_are_not_split(dou_items):
    v = by_id(dou_items, "378243")
    assert v.location == "Київ, Харків, Львів, Вроцлав (Польща), віддалено"
    assert v.is_remote is True


def test_double_escaped_ampersand_is_decoded(dou_items):
    v = by_id(dou_items, "378243")
    assert v.title == "Senior Python Backend Engineer (PostgreSQL & Knowledge Graphs)"


def test_onsite_vacancy_is_not_remote(dou_items):
    v = by_id(dou_items, "369404")  # Preply, Київ
    assert v.company == "Preply"
    assert v.location == "Київ"
    assert v.is_remote is False


def test_remote_word_in_description_does_not_make_it_remote(dou_items):
    # Mobilunity Belgium: hybrid, description says "1 day remote".
    v = by_id(dou_items, "378034")
    assert "remote" in v.description_text.lower()
    assert v.is_remote is False


def test_guid_and_url_are_canonical(dou_items):
    v = by_id(dou_items, "378476")
    expected = "https://jobs.dou.ua/companies/7perpendiculars/vacancies/378476/"
    assert v.guid == expected
    assert v.url == expected


def test_published_at_is_utc(dou_items):
    v = by_id(dou_items, "378476")  # Sat, 31 Oct 2026 12:11:19 +0200
    assert v.published_at == datetime(2026, 10, 31, 10, 11, 19, tzinfo=UTC)


def test_description_is_clean_text(dou_items):
    v = by_id(dou_items, "378476")
    text = v.description_text
    assert "<" not in text and "&lt;" not in text
    assert "Відгукнутись на вакансію" not in text
    assert "\xa0" not in text
    assert "• Design, build, and maintain Python-based backend applications" in text


# --- edge cases ----------------------------------------------------------------


@pytest.fixture
def edge_items(load_fixture):
    return parse_feed(load_fixture("dou_edge_cases.xml"))


def test_position_containing_v_is_split_on_last_separator(edge_items):
    v = edge_items[0]
    assert v.title == "Розробник в команду платформи"
    assert v.company == "Acme"
    assert v.salary_text == "до $5000"
    assert v.location == "Вроцлав (Польща), Львів"
    assert v.is_remote is False


def test_remote_detected_from_title(edge_items):
    v = edge_items[1]
    assert v.company == "Beta & Sons"
    assert v.salary_text == "від $2000"
    assert v.is_remote is True


def test_duplicate_guid_within_one_feed_is_dropped(edge_items):
    guids = [v.guid for v in edge_items]
    assert len(guids) == len(set(guids)) == 3


def test_list_markup_and_scripts(edge_items):
    assert edge_items[1].description_text == "Requirements\n• Python\n• Django"


def test_missing_location_salary_and_date(edge_items):
    v = edge_items[2]
    assert (v.title, v.company, v.salary_text, v.location) == ("QA Engineer", "Gamma", "", "")
    assert v.description_text == ""
    assert v.published_at.tzinfo is not None


def test_invalid_feed_raises():
    with pytest.raises(FeedParseError):
        parse_feed(b"<html><body>Cloudflare says no</body></html>")


# --- helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://jobs.dou.ua/companies/a/vacancies/1/?utm_source=jobsrss",
            "https://jobs.dou.ua/companies/a/vacancies/1/",
        ),
        (
            "https://jobs.dou.ua/companies/a/vacancies/1/?1793441479",
            "https://jobs.dou.ua/companies/a/vacancies/1/",
        ),
        (
            "https://jobs.dou.ua/companies/a/vacancies/1",
            "https://jobs.dou.ua/companies/a/vacancies/1/",
        ),
    ],
)
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dev в Co, Київ", ("Dev", "Co", "", ["Київ"])),
        ("Dev в Co, $1000, Київ", ("Dev", "Co", "$1000", ["Київ"])),
        ("Dev в Co, 30000–40000 грн, Київ", ("Dev", "Co", "30000–40000 грн", ["Київ"])),
        ("Dev without company", ("Dev without company", "", "", [])),
    ],
)
def test_split_title(title, expected):
    assert split_title(title) == expected


def test_clean_text_unescapes_until_stable():
    assert clean_text("A &amp;amp; B\xa0 C") == "A & B C"


@pytest.mark.parametrize(
    ("position", "locations", "expected"),
    [
        ("Dev", ["Київ", "віддалено"], True),
        ("Dev (Remote)", ["за кордоном"], True),
        ("Remoteness Engineer", ["Київ"], False),  # word boundary
        ("Dev", ["Київ"], False),
    ],
)
def test_detect_remote(position, locations, expected):
    assert detect_remote(position, locations) is expected


def test_html_to_text_handles_empty():
    assert html_to_text("") == ""
