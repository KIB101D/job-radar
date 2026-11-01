"""
What a vacancy offers, read from the fields DOU gives us: seniority from the title,
salary from the salary text, cities from the location.

Pure functions on strings, cached, used at match time (like keywords), so every
vacancy already in the database gets them and improving a rule needs no backfill.
"""

import re
from functools import lru_cache

# --- seniority ---------------------------------------------------------------------------

LEVELS = ("intern", "junior", "middle", "senior", "lead")
LEVEL_LABELS = {
    "intern": "Intern",
    "junior": "Junior",
    "middle": "Middle",
    "senior": "Senior",
    "lead": "Lead",
}
_LEVEL_WORDS = {
    "intern": r"intern(?:ship)?|trainee|стажер\w*|стажування",
    "junior": r"junior\+?|jun|jr\.?|джуніор\w*|джун|молодш\w*",
    "middle": r"middle\+?|mid(?:-level)?|мідл\w*",
    "senior": r"senior\+?|sr\.?|сеньйор\w*|сеніор\w*|старш\w*",
    "lead": r"(?:team|tech)[\s-]?lead|lead|principal|head\s+of|тімлід\w*|техлід\w*|лід",
}
_LEVEL_RES = {
    level: re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE)
    for level, words in _LEVEL_WORDS.items()
}


@lru_cache(maxsize=4096)
def levels_of(title: str) -> frozenset[str]:
    """
    "Middle+ / Senior Python Developer" -> {"middle", "senior"}.
    Empty when the title doesn't say; the level filter then lets the vacancy through,
    because "Python Developer" can be any level.
    """
    return frozenset(level for level, regex in _LEVEL_RES.items() if regex.search(title or ""))


# --- salary ------------------------------------------------------------------------------

# Rough rates, only to compare "від $1000" with a salary in another currency. DOU
# lists nearly all salaries in dollars; precision here doesn't matter.
USD_PER = {"$": 1.0, "€": 1.1, "грн": 1 / 41}
_NUMBER_RE = re.compile(r"\d[\d\s]*")


@lru_cache(maxsize=4096)
def salary_range(text: str) -> tuple[int | None, int | None] | None:
    """
    DOU salary text -> (low, high) in USD, None if there is no salary.
      "$4000–7000" -> (4000, 7000)   "$3000" -> (3000, 3000)
      "від $2000"  -> (2000, None)   "до $5000" -> (None, 5000)
      "30000–40000 грн" -> (732, 976)
    """
    text = (text or "").strip().lower()
    numbers = [int(re.sub(r"\s", "", n)) for n in _NUMBER_RE.findall(text)]
    if not numbers:
        return None
    if "грн" in text or "uah" in text:
        rate = USD_PER["грн"]
    elif "€" in text or "eur" in text:
        rate = USD_PER["€"]
    else:
        rate = USD_PER["$"]
    low, high = (round(n * rate) for n in (numbers[0], numbers[-1]))
    if text.startswith("від"):
        return low, None
    if text.startswith("до"):
        return None, high
    return low, high


def salary_reaches(text: str, minimum: int) -> bool | None:
    """
    Can this vacancy pay `minimum` USD? None if it names no salary.
    Judged by the top of the range ("$600–800" doesn't reach $1000, "до $5000"
    does); "від $2000" only says $2000 for sure.
    """
    found = salary_range(text)
    if found is None:
        return None
    low, high = found
    return (high if high is not None else low) >= minimum


# --- cities ------------------------------------------------------------------------------

REMOTE_LOCATION = "віддалено"


def split_outside_parentheses(text: str) -> list[str]:
    """Split on commas, but not inside "(...)": "Вроцлав (Польща)" stays whole."""
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


@lru_cache(maxsize=4096)
def cities_of(location: str) -> tuple[str, ...]:
    """
    "Київ, Львів, за кордоном, віддалено" -> ("Київ", "Львів", "За кордоном").
    "віддалено" isn't a city: it's the separate "Віддалено" option.
    """
    return tuple(
        part[:1].upper() + part[1:]
        for part in split_outside_parentheses(location or "")
        if part.lower() != REMOTE_LOCATION
    )
