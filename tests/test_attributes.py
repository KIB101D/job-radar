"""Level, salary and cities read from DOU's title, salary text and location."""

import pytest

from radar.attributes import cities_of, levels_of, salary_range, salary_reaches


@pytest.mark.parametrize(
    ("title", "levels"),
    [
        ("Junior Python Developer", {"junior"}),
        ("Junior Strong Python Developer", {"junior"}),
        ("Python-розробник (Middle+ / Senior)", {"middle", "senior"}),
        ("Full-Stack Developer Python/Vue.js (middle)", {"middle"}),
        ("Integration Tech Lead", {"lead"}),
        ("Team Lead Python", {"lead"}),
        ("Head of Engineering", {"lead"}),
        ("Trainee QA Engineer", {"intern"}),
        ("Стажер-розробник", {"intern"}),
        ("Старший Python розробник", {"senior"}),
        ("Sr. Backend Engineer", {"senior"}),
        ("Python Software Engineer", set()),
        ("Junior Django Developer, June start", {"junior"}),  # "June" isn't "jun"
        ("Seniority-free Engineer", set()),  # no partial words
    ],
)
def test_levels_of(title, levels):
    assert levels_of(title) == frozenset(levels)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$4000–7000", (4000, 7000)),
        ("$600–800", (600, 800)),
        ("$3000", (3000, 3000)),
        ("від $2000", (2000, None)),
        ("до $5000", (None, 5000)),
        ("$1 500–2 000", (1500, 2000)),
        ("€2000–3000", (2200, 3300)),
        ("41000–82000 грн", (1000, 2000)),
        ("", None),
        ("за домовленістю", None),
    ],
)
def test_salary_range(text, expected):
    assert salary_range(text) == expected


def test_salary_reaches():
    assert salary_reaches("$600–800", 1000) is False
    assert salary_reaches("до $5000", 1000) is True
    assert salary_reaches("", 1000) is None


def test_cities_of():
    assert cities_of("Київ, Львів, Вроцлав (Польща), за кордоном, віддалено") == (
        "Київ",
        "Львів",
        "Вроцлав (Польща)",
        "За кордоном",
    )
    assert cities_of("віддалено") == ()
    assert cities_of("") == ()
