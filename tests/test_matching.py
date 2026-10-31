import pytest

from radar.matching import Filters, compile_keywords, matches, parse_keywords


def check(filters, title="", description="", is_remote=False, categories=("Python",)):
    return matches(
        filters, title=title, description=description, is_remote=is_remote, categories=categories
    )


# --- include / exclude ----------------------------------------------------------


def test_no_filters_match_everything():
    assert check(Filters(), title="Anything")


def test_include_needs_at_least_one_keyword():
    f = Filters(include=("django", "fastapi"))
    assert check(f, title="Backend dev", description="We use FastAPI")
    assert not check(f, title="Backend dev", description="We use Flask")


def test_exclude_wins_over_include():
    f = Filters(include=("python",), exclude=("senior",))
    assert check(f, title="Junior Python Developer")
    assert not check(f, title="Senior Python Developer")


def test_exclude_also_checks_description():
    f = Filters(exclude=("php",))
    assert not check(f, title="Odoo developer", description="Python\nPHP")


def test_case_insensitive_including_cyrillic():
    assert check(Filters(include=("розробник",)), title="Python-РОЗРОБНИК (Middle+)")


# --- word boundaries ------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "text", "expected"),
    [
        ("java", "Senior JavaScript Developer", False),
        ("java", "Java/Kotlin Developer", True),
        ("react", "Reactive programming", False),
        ("react", "React/TypeScript", True),
        ("c++", "Strong C++ skills", True),
        ("c++", "C++17", False),
        ("c#", "C# and .NET", True),
        ("node.js", "Node.js, NestJS", True),
        ("js", "Node.js developer", True),
        ("go", "Google Cloud", False),
        ("go", "Python, Go, Rust", True),
        ("ai", "Agentic AI Engineer", True),
        ("ai", "Maintain clean code", False),
        ("junior", "Junior Strong Python Developer", True),
        ("python", "Python-розробник", True),
        ("react native", "React   Native developer", True),
        ("react native", "React developer, native apps", False),
    ],
)
def test_word_boundaries(keyword, text, expected):
    assert check(Filters(include=(keyword,)), title=text) is expected


def test_longer_phrase_preferred_in_alternation():
    pattern = compile_keywords(("react", "react native"))
    assert pattern.search("React Native").group(0) == "React Native"


# --- remote & categories ---------------------------------------------------------


def test_remote_only():
    f = Filters(remote_only=True)
    assert check(f, is_remote=True)
    assert not check(f, is_remote=False)


def test_categories_any_overlap():
    f = Filters(categories=frozenset({"JavaScript", "Node.js"}))
    assert check(f, categories=["Front End", "JavaScript"])
    assert not check(f, categories=["Python"])


def test_empty_categories_means_all():
    assert check(Filters(categories=frozenset()), categories=["Anything"])


def test_all_rules_combined():
    f = Filters(
        include=("python", "django"),
        exclude=("senior", "lead"),
        categories=frozenset({"Python"}),
        remote_only=True,
    )
    assert check(f, title="Junior Python Developer", is_remote=True)
    assert not check(f, title="Junior Python Developer", is_remote=False)
    assert not check(f, title="Python Tech Lead", is_remote=True)
    assert not check(f, title="Junior Python Developer", is_remote=True, categories=["QA"])


# --- parse_keywords --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python, Django", ["python", "django"]),
        ("python;django\nfastapi", ["python", "django", "fastapi"]),
        ("  React   Native , react native ", ["react native"]),
        (",, ,", []),
        ("", []),
        (["C++", " c++ "], ["c++"]),
    ],
)
def test_parse_keywords(raw, expected):
    assert parse_keywords(raw) == expected


def test_filters_from_subscription():
    from types import SimpleNamespace

    sub = SimpleNamespace(
        include_keywords=["python"], exclude_keywords=[], categories=["Python"], remote_only=True
    )
    assert Filters.from_subscription(sub) == Filters(
        include=("python",), categories=frozenset({"Python"}), remote_only=True
    )
