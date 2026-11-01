"""
The settings panel: ONE Telegram message with inline buttons, edited in place.

Screens (ChatState.panel_screen). Each menu button opens its own top screen
directly, without a summary screen in between:

    🔎 Фільтри    filters ──┬── words     (include keywords)
                           ├── exclude   (exclude keywords)
                           ├── cats      (DOU categories)
                           ├── levels    (Intern ... Lead)
                           ├── places    (remote + cities)
                           └── salary    (minimum + vacancies without one)
    ⏰ Сповіщення  freq                    (frequency + quiet hours)

Opening the panel again (menu button, /filters, /frequency) deletes
the old panel and sends a fresh one at the bottom of the chat, so there's only
ever one. Lists of vacancies, pause and digests stay separate messages: those
are events worth keeping in the history.

Every button sets an absolute value (cat+/cat-, rem:on/off, sal:1000, quiet:on/off) and
word removal checks a fingerprint of the word, so pressing a button in a panel
that's out of date can never undo a change made on the site.
"""

import zlib
from html import escape

from radar.attributes import LEVEL_LABELS, LEVELS
from radar.matching import MAX_KEYWORD_LENGTH, MAX_KEYWORDS, parse_keywords
from radar.search import search
from subscriptions import schedule
from subscriptions.delivery import filters_for
from subscriptions.forms import FILTER_FIELDS, MAX_CITIES, category_choices, city_choices
from subscriptions.models import Frequency, Subscription

from . import texts
from .formatting import is_public_url, levels_text, places_text, radar_url, salary_label
from .models import ChatState
from .telegram import TelegramError, get_client

SCREENS = ("filters", "words", "exclude", "cats", "levels", "places", "salary", "freq")
SALARY_PRESETS = (500, 1000, 2000, 3000)
CALLBACK_LIMIT = 64  # bytes of callback_data Telegram accepts
# The summary screen was removed; buttons in older panels still say "nav:main".
SCREEN_ALIASES = {"main": "filters"}
WORD_LISTS = {"words": "include_keywords", "exclude": "exclude_keywords"}
LIST_CODES = {"inc": "words", "exc": "exclude"}  # short codes for callback_data (64 bytes max)
CODE_OF = {v: k for k, v in LIST_CODES.items()}

# Telegram's answers when a message can't be edited or deleted any more.
GONE = (
    "message to edit not found",
    "message can't be edited",
    "message to delete not found",
    "message can't be deleted",
    "message_id_invalid",
)


def _gone(exc: TelegramError) -> bool:
    return any(marker in exc.description.lower() for marker in GONE)


CLOSE_BUTTON = {"text": texts.BTN_CLOSE, "callback_data": "close"}


def fingerprint(word: str) -> str:
    return f"{zlib.crc32(word.encode()):08x}"


def match_count(subscription: Subscription) -> int:
    return search(filters_for(subscription), limit=1).total


def state_for(chat_id: int) -> ChatState:
    state, _ = ChatState.objects.get_or_create(chat_id=chat_id)
    return state


# --- rendering ------------------------------------------------------------------------------------


def render(subscription: Subscription, screen: str, note: str = "") -> tuple[str, dict]:
    screen = SCREEN_ALIASES.get(screen, screen)
    builder = {
        "filters": _filters,
        "words": _words,
        "exclude": _words,
        "cats": _categories,
        "levels": _levels,
        "places": _places,
        "salary": _salary,
        "freq": _frequency,
    }.get(screen, _filters)
    text, rows = builder(subscription, screen)
    if note:
        text += "\n\n" + note
    return text, {"inline_keyboard": rows}


def _filter_lines(subscription: Subscription) -> list[str]:
    def join(values, empty):
        return escape(", ".join(values)) if values else empty

    return [
        texts.LINE_WORDS.format(value=join(subscription.include_keywords, texts.ANY_WORDS)),
        texts.LINE_EXCLUDE.format(value=join(subscription.exclude_keywords, texts.NO_EXCLUDE)),
        texts.LINE_CATEGORIES.format(
            value=join(sorted(subscription.categories), texts.ALL_CATEGORIES)
        ),
        texts.LINE_LEVELS.format(value=levels_text(subscription.levels) or texts.ANY_LEVEL),
        texts.LINE_PLACES.format(
            value=escape(places_text(subscription.remote_only, subscription.cities))
            or texts.ANY_PLACE
        ),
        texts.LINE_SALARY.format(
            value=salary_label(subscription.salary_min, subscription.salary_unknown)
            or texts.ANY_SALARY
        ),
    ]


def _filters(subscription: Subscription, screen: str):
    text = "\n".join(
        [
            texts.FILTERS_TITLE,
            *_filter_lines(subscription),
            "",
            texts.MATCHES.format(count=match_count(subscription)),
        ]
    )
    rows = [
        [
            {"text": texts.BTN_WORDS, "callback_data": "nav:words"},
            {"text": texts.BTN_EXCLUDE, "callback_data": "nav:exclude"},
        ],
        [
            {"text": texts.BTN_CATEGORIES, "callback_data": "nav:cats"},
            {"text": texts.BTN_LEVELS, "callback_data": "nav:levels"},
        ],
        [
            {"text": texts.BTN_PLACES, "callback_data": "nav:places"},
            {"text": texts.BTN_SALARY, "callback_data": "nav:salary"},
        ],
    ]
    url = radar_url(subscription)
    if is_public_url(url):  # Telegram won't accept a localhost button in development
        rows.append([{"text": texts.BTN_SITE, "url": url}])
    rows.append([CLOSE_BUTTON])
    return text, rows


def _words(subscription: Subscription, screen: str):
    words = getattr(subscription, WORD_LISTS[screen])
    is_include = screen == "words"
    lines = [
        texts.WORDS_TITLE if is_include else texts.EXCLUDE_TITLE,
        texts.WORDS_HELP if is_include else texts.EXCLUDE_HELP,
        "",
        escape(", ".join(words))
        if words
        else (texts.WORDS_EMPTY if is_include else texts.EXCLUDE_EMPTY),
        "",
        texts.ADD_HINT.format(example="fastapi, junior" if is_include else "senior, php"),
        texts.MATCHES.format(count=match_count(subscription)),
    ]
    code = CODE_OF[screen]
    buttons = [
        {
            "text": texts.BTN_REMOVE_WORD.format(word=word),
            "callback_data": f"kwdel:{code}:{index}:{fingerprint(word)}",
        }
        for index, word in enumerate(words)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if words:
        rows.append([{"text": texts.BTN_CLEAR_WORDS, "callback_data": f"kwclr:{code}"}])
    rows.append([{"text": texts.BTN_BACK_FILTERS, "callback_data": "nav:filters"}])
    return "\n".join(lines), rows


def _categories(subscription: Subscription, screen: str):
    chosen = set(subscription.categories)
    text = "\n".join(
        [
            texts.CATEGORIES_TITLE,
            texts.CATEGORIES_HELP,
            "",
            texts.MATCHES.format(count=match_count(subscription)),
        ]
    )
    buttons = []
    for value, label in category_choices():
        on = value in chosen
        buttons.append(
            {
                "text": (texts.SELECTED if on else "") + label,
                "callback_data": f"cat{'-' if on else '+'}:{value}",
            }
        )
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if chosen:
        rows.append([{"text": texts.BTN_ALL_CATEGORIES, "callback_data": "catall"}])
    rows.append([{"text": texts.BTN_BACK_FILTERS, "callback_data": "nav:filters"}])
    return text, rows


def _check(on: bool, label: str, data: str) -> dict:
    return {"text": (texts.SELECTED if on else "") + label, "callback_data": data}


def _screen(title: str, help_text: str, subscription: Subscription) -> str:
    return "\n".join([title, help_text, "", texts.MATCHES.format(count=match_count(subscription))])


def _levels(subscription: Subscription, screen: str):
    chosen = set(subscription.levels)
    buttons = [
        _check(
            level in chosen, LEVEL_LABELS[level], f"lvl{'-' if level in chosen else '+'}:{level}"
        )
        for level in LEVELS
    ]
    rows = [buttons[:3], buttons[3:]]
    if chosen:
        rows.append([{"text": texts.BTN_ANY_LEVEL, "callback_data": "lvlall"}])
    rows.append([{"text": texts.BTN_BACK_FILTERS, "callback_data": "nav:filters"}])
    return _screen(texts.LEVELS_TITLE, texts.LEVELS_HELP, subscription), rows


def _places(subscription: Subscription, screen: str):
    remote = subscription.remote_only
    chosen = set(subscription.cities)
    rows = [[_check(remote, texts.BTN_REMOTE_CHIP, f"rem:{'off' if remote else 'on'}")]]
    buttons = []
    for city, _ in city_choices(subscription.cities):
        data = f"plc{'-' if city in chosen else '+'}:{city}"
        if len(data.encode()) <= CALLBACK_LIMIT:
            buttons.append(_check(city in chosen, city, data))
    rows += [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if remote or chosen:
        rows.append([{"text": texts.BTN_ANY_PLACE, "callback_data": "plcall"}])
    rows.append([{"text": texts.BTN_BACK_FILTERS, "callback_data": "nav:filters"}])
    return _screen(texts.PLACES_TITLE, texts.PLACES_HELP, subscription), rows


def _salary(subscription: Subscription, screen: str):
    current = subscription.salary_min
    presets = [_check(not current, texts.BTN_ANY_SALARY, "sal:0")] + [
        _check(current == amount, texts.BTN_SALARY_FROM.format(amount=amount), f"sal:{amount}")
        for amount in SALARY_PRESETS
    ]
    unknown = (
        {"text": texts.BTN_UNKNOWN_ON, "callback_data": "sunk:off"}
        if subscription.salary_unknown
        else {"text": texts.BTN_UNKNOWN_OFF, "callback_data": "sunk:on"}
    )
    rows = [presets[:3], presets[3:], [unknown]]
    rows.append([{"text": texts.BTN_BACK_FILTERS, "callback_data": "nav:filters"}])
    return _screen(texts.SALARY_TITLE, texts.SALARY_HELP, subscription), rows


def frequency_text(subscription: Subscription) -> str:
    lines = [
        texts.FREQUENCY_TITLE,
        texts.FREQUENCY_CURRENT.format(label=subscription.get_frequency_display()),
    ]
    when = schedule.next_slot_label(subscription)
    if subscription.paused:
        lines.append(texts.FREQUENCY_PAUSED.format(button=texts.BTN_RESUME))
    elif when:
        lines.append(
            texts.FREQUENCY_NEXT.format(when=when, tz=schedule.timezone_label(subscription))
        )
    else:
        lines.append(texts.FREQUENCY_OFF_HINT.format(button=texts.BTN_CHECK))
    if subscription.frequency != Frequency.OFF:
        if subscription.quiet_hours_enabled:
            lines.append(
                texts.QUIET_ON.format(
                    start=f"{subscription.quiet_hours_start:02d}:00",
                    end=f"{subscription.quiet_hours_end:02d}:00",
                )
            )
        else:
            lines.append(texts.QUIET_OFF)
    return "\n".join(lines)


def _frequency(subscription: Subscription, screen: str):
    buttons = [
        {
            "text": (texts.SELECTED if value == subscription.frequency else "") + label,
            "callback_data": f"freq:{value}",
        }
        for value, label in Frequency.choices
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    # Absolute values, not a toggle: an old panel still does what its button says.
    if subscription.quiet_hours_enabled:
        rows.append([{"text": texts.QUIET_BUTTON_ON, "callback_data": "quiet:off"}])
    else:
        rows.append([{"text": texts.QUIET_BUTTON_OFF, "callback_data": "quiet:on"}])
    rows.append([CLOSE_BUTTON])
    return frequency_text(subscription), rows


# --- showing the panel ----------------------------------------------------------------------------


def open_panel(subscription: Subscription, screen: str, *, tap_message_id=None) -> None:
    """Replace the chat's panel with a fresh one at the bottom of the chat."""
    client = get_client()
    chat_id = subscription.chat_id
    state = state_for(chat_id)
    for message_id in (state.panel_message_id, tap_message_id):
        if message_id:
            _delete_quietly(chat_id, message_id)
    text, markup = render(subscription, screen)
    message = client.send_message(chat_id, text, markup)
    state.panel_message_id = message["message_id"]
    state.panel_screen = screen
    state.save()


def show(subscription: Subscription, message_id: int, screen: str, note: str = "") -> None:
    """Switch the panel (the message whose button was pressed) to `screen`."""
    text, markup = render(subscription, screen, note)
    get_client().edit_message_text(subscription.chat_id, message_id, text, markup)
    ChatState.objects.update_or_create(
        chat_id=subscription.chat_id,
        defaults={"panel_message_id": message_id, "panel_screen": screen},
    )


def refresh_panel(subscription: Subscription) -> bool:
    """
    Re-render the current panel screen from the database (used after changes made
    on the website). Best effort: never raises.
    """
    if not subscription.chat_id:
        return False
    state = ChatState.objects.filter(chat_id=subscription.chat_id).first()
    if state is None or not state.panel_message_id:
        return False
    text, markup = render(subscription, state.panel_screen)
    try:
        get_client().edit_message_text(subscription.chat_id, state.panel_message_id, text, markup)
    except TelegramError as exc:
        if _gone(exc):
            state.panel_message_id = None
            state.save()
        return False
    return True


def forget_panel(chat_id: int) -> None:
    """The chat is no longer linked: delete its settings panel, if there is one."""
    state = ChatState.objects.filter(chat_id=chat_id).first()
    if state and state.panel_message_id:
        _delete_quietly(chat_id, state.panel_message_id)
        state.panel_message_id = None
        state.save()


def say_unlinked(chat_id: int) -> None:
    """
    Unlinked on the website: tell the chat, take away the panel and the menu, so
    Telegram doesn't keep looking connected. Best effort: never raises.
    """
    forget_panel(chat_id)
    try:
        get_client().send_message(chat_id, texts.UNLINKED_ON_SITE, {"remove_keyboard": True})
    except TelegramError:
        pass  # the user blocked the bot, Telegram is down...: the unlink itself stands


def close_panel(subscription: Subscription, message_id: int) -> None:
    _delete_quietly(subscription.chat_id, message_id)
    ChatState.objects.filter(chat_id=subscription.chat_id, panel_message_id=message_id).update(
        panel_message_id=None
    )


def _delete_quietly(chat_id: int, message_id: int) -> None:
    """Old messages (48 h+) can't be deleted; that's fine, just leave them."""
    try:
        get_client().delete_message(chat_id, message_id)
    except TelegramError:
        pass


# --- changing filters -----------------------------------------------------------------------------


def _save_filters(subscription: Subscription) -> None:
    subscription.save(update_fields=[*FILTER_FIELDS, "updated_at"])


def add_words(subscription: Subscription, screen: str, raw: str) -> str:
    """Add comma-separated words to the include/exclude list. Returns a note."""
    field = WORD_LISTS[screen]
    current = list(getattr(subscription, field))
    too_long = [w for w in parse_keywords(raw) if len(w) > MAX_KEYWORD_LENGTH]
    fresh = [w for w in parse_keywords(raw) if len(w) <= MAX_KEYWORD_LENGTH and w not in current]
    room = MAX_KEYWORDS - len(current)
    added = fresh[: max(room, 0)]
    notes = []
    if added:
        setattr(subscription, field, current + added)
        _save_filters(subscription)
        notes.append(texts.NOTE_ADDED.format(words=escape(", ".join(added))))
    if too_long:
        notes.append(
            texts.NOTE_TOO_LONG.format(
                word=escape(too_long[0][:20]) + "…", limit=MAX_KEYWORD_LENGTH
            )
        )
    if len(fresh) > len(added):
        notes.append(texts.NOTE_TOO_MANY.format(limit=MAX_KEYWORDS))
    if not notes:
        notes.append(texts.NOTE_ALREADY)
    return "\n".join(notes)


def remove_word(subscription: Subscription, screen: str, index: int, check: str) -> str:
    field = WORD_LISTS[screen]
    words = list(getattr(subscription, field))
    if not (0 <= index < len(words)) or fingerprint(words[index]) != check:
        return texts.NOTE_STALE  # the list changed (e.g. on the site): don't guess
    word = words.pop(index)
    setattr(subscription, field, words)
    _save_filters(subscription)
    return texts.NOTE_REMOVED.format(word=escape(word))


def clear_words(subscription: Subscription, screen: str) -> str:
    setattr(subscription, WORD_LISTS[screen], [])
    _save_filters(subscription)
    return texts.NOTE_CLEARED


def set_category(subscription: Subscription, category: str, on: bool) -> None:
    valid = {value for value, _ in category_choices()}
    if category not in valid:
        return
    chosen = set(subscription.categories)
    chosen.add(category) if on else chosen.discard(category)
    subscription.categories = sorted(chosen)
    _save_filters(subscription)


def clear_categories(subscription: Subscription) -> None:
    subscription.categories = []
    _save_filters(subscription)


def set_remote(subscription: Subscription, on: bool) -> None:
    subscription.remote_only = on
    _save_filters(subscription)


def set_level(subscription: Subscription, level: str, on: bool) -> None:
    if level not in LEVELS:
        return
    chosen = set(subscription.levels)
    chosen.add(level) if on else chosen.discard(level)
    subscription.levels = [lv for lv in LEVELS if lv in chosen]
    _save_filters(subscription)


def set_city(subscription: Subscription, city: str, on: bool) -> None:
    if not 0 < len(city) <= 60:
        return
    cities = [c for c in subscription.cities if c != city]
    if on and len(cities) < MAX_CITIES:
        cities.append(city)
    subscription.cities = cities
    _save_filters(subscription)


def set_salary(subscription: Subscription, *, minimum=None, unknown=None) -> None:
    if minimum is not None:
        subscription.salary_min = minimum
    if unknown is not None:
        subscription.salary_unknown = unknown
    _save_filters(subscription)


# --- input ----------------------------------------------------------------------------------------


def handle_callback(subscription: Subscription, data: str, message_id: int) -> str | None:
    """
    Panel buttons. Returns the short text for answerCallbackQuery, "" for none,
    or None if `data` isn't a panel button.
    """
    kind, _, value = data.partition(":")
    note = ""
    if kind == "nav" and (value in SCREENS or value in SCREEN_ALIASES):
        screen = SCREEN_ALIASES.get(value, value)
    elif kind == "close":
        close_panel(subscription, message_id)
        return ""
    elif kind == "freq" and value in Frequency.values:
        subscription.frequency = value
        subscription.save(update_fields=["frequency", "updated_at"])
        screen = "freq"
    elif kind == "quiet" and value in ("on", "off"):
        subscription.quiet_hours_enabled = value == "on"
        subscription.save(update_fields=["quiet_hours_enabled", "updated_at"])
        screen = "freq"
    elif kind == "remote" and value in (
        "on",
        "off",
    ):  # "Віддалено" on the filters screen of old panels
        set_remote(subscription, value == "on")
        screen = "filters"
    elif kind == "rem" and value in ("on", "off"):
        set_remote(subscription, value == "on")
        screen = "places"
    elif kind in ("plc+", "plc-") and value:
        set_city(subscription, value, on=kind == "plc+")
        screen = "places"
    elif kind == "plcall":
        subscription.cities, subscription.remote_only = [], False
        _save_filters(subscription)
        screen = "places"
    elif kind in ("lvl+", "lvl-") and value:
        set_level(subscription, value, on=kind == "lvl+")
        screen = "levels"
    elif kind == "lvlall":
        subscription.levels = []
        _save_filters(subscription)
        screen = "levels"
    elif kind == "sal" and value.isdigit() and int(value) <= 100_000:
        set_salary(subscription, minimum=int(value))
        screen = "salary"
    elif kind == "sunk" and value in ("on", "off"):
        set_salary(subscription, unknown=value == "on")
        screen = "salary"
    elif kind in ("cat+", "cat-") and value:
        set_category(subscription, value, on=kind == "cat+")
        screen = "cats"
    elif kind == "catall":
        clear_categories(subscription)
        screen = "cats"
    elif kind == "kwdel":
        code, _, rest = value.partition(":")
        index, _, check = rest.partition(":")
        if code not in LIST_CODES or not index.isdigit():
            return None
        screen = LIST_CODES[code]
        note = remove_word(subscription, screen, int(index), check)
    elif kind == "kwclr" and value in LIST_CODES:
        screen = LIST_CODES[value]
        note = clear_words(subscription, screen)
    else:
        return None
    show(subscription, message_id, screen, note)
    return texts.FREQUENCY_SAVED if kind in ("freq", "quiet") else ""


def handle_text(subscription: Subscription, text: str, message_id: int) -> bool:
    """
    Typed text while the panel shows a word list = words to add. The typed message
    is deleted and the panel is updated in place. Returns False if not consumed.
    """
    state = ChatState.objects.filter(chat_id=subscription.chat_id).first()
    if state is None or state.panel_screen not in WORD_LISTS or not state.panel_message_id:
        return False
    note = add_words(subscription, state.panel_screen, text)
    _delete_quietly(subscription.chat_id, message_id)
    try:
        show(subscription, state.panel_message_id, state.panel_screen, note)
    except TelegramError as exc:
        if not _gone(exc):
            raise
        open_panel(subscription, state.panel_screen)  # the panel was deleted: send a new one
    return True
