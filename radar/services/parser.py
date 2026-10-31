"""
Parse DOU vacancy RSS into plain dataclasses. Pure: no DB, no network.

DOU item format (as of Oct 2026), learned from a real feed (tests/fixtures):

    <title>{position} в {company}, [{salary}, ]{location}[, {location}...]</title>
    <link>https://jobs.dou.ua/companies/{slug}/vacancies/{id}/?utm_source=jobsrss</link>
    <guid>https://jobs.dou.ua/companies/{slug}/vacancies/{id}/?{unix_ts}</guid>
    <description>HTML ... + an "Відгукнутись на вакансію" link at the end</description>

Quirks handled here:
- <guid> ends with "?<timestamp>" that changes when a vacancy is bumped, so we
  dedupe on the canonical URL (path only) instead.
- Titles are double-escaped ("&amp;amp;"), so we unescape until stable.
- DOU typography uses non-breaking spaces everywhere.
"""

import calendar
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup, NavigableString

TITLE_MAX = 300
COMPANY_MAX = 200
LOCATION_MAX = 300
SALARY_MAX = 100

REMOTE_LOCATION = "віддалено"
_REMOTE_TITLE_RE = re.compile(r"\bremote\b", re.IGNORECASE)

# "$4000–7000", "$600–800", "до $5000", "від $3000", "$3000", "€2000–3000", "30000–40000 грн"
_SALARY_RE = re.compile(
    r"^(?:(?:від|до)\s+)?[$€]?\s?\d[\d\s]*(?:\s?[–-]\s?\d[\d\s]*)?(?:\s?(?:грн|uah|usd|eur))?$",
    re.IGNORECASE,
)

_BLOCK_TAGS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "tr", "ul", "ol"]


@dataclass(frozen=True)
class ParsedVacancy:
    guid: str
    url: str
    title: str
    company: str
    location: str
    is_remote: bool
    salary_text: str
    description_text: str
    published_at: datetime


class FeedParseError(Exception):
    pass


def parse_feed(content: bytes | str) -> list[ParsedVacancy]:
    feed = feedparser.parse(content)
    # feedparser is lenient: an HTML page (e.g. a bot-protection challenge served
    # with HTTP 200) parses as an "empty feed". Treat that as an error instead of
    # silently recording a successful fetch with zero items.
    if not feed.entries and (feed.bozo or not feed.get("version")):
        reason = feed.get("bozo_exception") or "no RSS/Atom root element"
        raise FeedParseError(f"Not a valid RSS feed: {reason!r}")

    result: list[ParsedVacancy] = []
    seen: set[str] = set()
    for entry in feed.entries:
        item = parse_entry(entry)
        if item is None or item.guid in seen:
            continue
        seen.add(item.guid)
        result.append(item)
    return result


def parse_entry(entry) -> ParsedVacancy | None:
    link = entry.get("link") or entry.get("id") or ""
    if not link:
        return None
    url = canonical_url(link)

    position, company, salary, locations = split_title(entry.get("title", ""))
    location = ", ".join(locations)

    return ParsedVacancy(
        guid=url,
        url=url,
        title=position[:TITLE_MAX],
        company=company[:COMPANY_MAX],
        location=location[:LOCATION_MAX],
        is_remote=detect_remote(position, locations),
        salary_text=salary[:SALARY_MAX],
        description_text=html_to_text(entry.get("summary", "")),
        published_at=_published_at(entry),
    )


def canonical_url(url: str) -> str:
    """Drop query and fragment: ?utm_source=jobsrss and ?<timestamp> are noise."""
    parts = urlsplit(url.strip())
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit((parts.scheme or "https", parts.netloc, path, "", ""))


def clean_text(value: str) -> str:
    """Unescape repeatedly (DOU double-escapes titles) and normalise whitespace."""
    previous = None
    while previous != value:
        previous, value = value, html.unescape(value)
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def split_title(raw_title: str) -> tuple[str, str, str, list[str]]:
    """
    "Python Dev в Acme, $3000–4000, Київ, віддалено"
        -> ("Python Dev", "Acme", "$3000–4000", ["Київ", "віддалено"])

    The LAST " в " separates position from the rest, because positions can
    contain " в " themselves ("Розробник в команду Платформи в Acme, Київ").
    """
    title = clean_text(raw_title)
    position, sep, rest = title.rpartition(" в ")
    if not sep:
        return title, "", "", []

    parts = _split_outside_parentheses(rest)
    company = parts[0] if parts else ""
    salary = ""
    locations: list[str] = []
    for part in parts[1:]:
        if not salary and _SALARY_RE.match(part):
            salary = part
        else:
            locations.append(part)
    return position.strip(), company, salary, locations


def _split_outside_parentheses(text: str) -> list[str]:
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


def detect_remote(position: str, locations: list[str]) -> bool:
    """
    Remote if DOU lists "віддалено" as a location, or the title says "Remote".
    The description is deliberately NOT used: phrases like "1 day remote" in a
    hybrid/onsite job would create false positives.
    """
    if any(loc.lower() == REMOTE_LOCATION for loc in locations):
        return True
    return bool(_REMOTE_TITLE_RE.search(position))


def html_to_text(raw_html: str) -> str:
    """Readable plain text: keeps paragraph/list structure, drops the reply button."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")

    for anchor in soup.select('a[href*="#reply-btn-id"]'):
        parent = anchor.parent
        (parent if parent is not None and parent.name == "div" else anchor).decompose()
    for tag in soup(["script", "style"]):
        tag.decompose()

    for br in soup.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for li in soup.find_all("li"):
        li.insert(0, NavigableString("• "))
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append(NavigableString("\n"))

    text = soup.get_text().replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _published_at(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        # feedparser normalises to a UTC struct_time.
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return datetime.now(tz=UTC)
