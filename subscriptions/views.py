"""
Website. Pages only read from our DB; scraping happens in background jobs.

Pages (the top navigation: Головна, Фільтри, Telegram-бот):
- /                       Головна: hero with a radar of the newest vacancies, how it works
- /filters/               Фільтри without a radar: form + live preview; saving creates one
- /s/<token>/             Фільтри of a radar: filters, live matches, fresh feed
- /bot/                   Telegram-бот without a radar: what the bot does, how to start
- /s/<token>/bot/         Telegram-бот of a radar: connect, or frequency and quiet hours
- /s/<token>/connect/     step 2: open the bot (deep link, QR)
- /s/<token>/frequency/   step 3: how often to notify
/filters/ and /bot/ redirect to the radar remembered in the cookie, if any.

HTMX fragments:
- preview/                matches for unsaved filters on /filters/ (before a radar exists)
- POST s/<token>/         (HTMX) auto-saves ONE changed filter, returns the new matches
- s/<token>/filters-form/ re-reads the filters from the DB when the tab is visible again
- s/<token>/results/      results list ("Усі" or "Нові"), first page or "Показати ще"
- s/<token>/live/         polled every 60 s: "Нові · N" tab, last-fetch stamp, toast
- s/<token>/link-status/  polled on the connect page until the chat is linked
- s/<token>/next-slot/    "Наступне сповіщення о 18:00" for unsaved frequency choices
"""

import math
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Max
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from radar.matching import Filters
from radar.models import Source
from radar.search import fresh_feed, latest_vacancy_id, newer_than, search

from . import schedule
from .forms import FILTER_FIELDS, FiltersForm, NotificationsForm, category_choices
from .models import Delivery, Subscription

TOKEN_COOKIE = "jr_token"
TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
PAGE_SIZE = 20
PREVIEW_SIZE = 8
FEED_SIZE = 6
HERO_BLIPS = 9
FRESH_HOURS = 24
VIEW_ALL, VIEW_NEW = "all", "new"


# --- helpers ------------------------------------------------------------------------


def _remember_token(response, token: str):
    response.set_cookie(
        TOKEN_COOKIE,
        token,
        max_age=TOKEN_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=not settings.DEBUG,
    )
    return response


def _bot_username() -> str:
    return settings.TELEGRAM_BOT_USERNAME


def _deep_link(subscription: Subscription) -> str:
    """Opening it sends `/start <token>` to the bot, which links the chat."""
    username = _bot_username()
    return f"https://t.me/{username}?start={subscription.token}" if username else ""


def _delivered_ids(subscription: Subscription | None, vacancies) -> set[int]:
    if subscription is None or not subscription.chat_id or not vacancies:
        return set()
    return set(
        Delivery.objects.filter(
            subscription=subscription, vacancy_id__in=[v.pk for v in vacancies]
        ).values_list("vacancy_id", flat=True)
    )


def last_fetched_at():
    """When vacancies were last refreshed from DOU (any source)."""
    return Source.objects.aggregate(m=Max("last_fetched_at"))["m"]


def _fresh_after():
    return timezone.now() - timedelta(hours=FRESH_HOURS)


def _search(subscription, filters: Filters, *, view=VIEW_ALL, offset=0, limit=PAGE_SIZE):
    """
    "Усі" = every match; "Нові" = what the cards mark НОВА: first seen in the last
    FRESH_HOURS and not sent to this radar's Telegram chat yet.
    """
    if view == VIEW_NEW and subscription is not None:
        return search(
            filters,
            offset=offset,
            limit=limit,
            fresh_after=_fresh_after(),
            skip_delivered_to=subscription,
        )
    return search(filters, offset=offset, limit=limit)


def _view_param(request) -> str:
    return VIEW_NEW if request.GET.get("view") == VIEW_NEW else VIEW_ALL


def _results_context(subscription, filters: Filters, page, **extra) -> dict:
    context = {
        "subscription": subscription,
        "filters": filters,
        "page": page,
        "view": VIEW_ALL,
        "remaining": max(page.total - page.next_offset, 0),
        "seen_ids": _delivered_ids(subscription, page.vacancies),
        "fresh_after": _fresh_after(),
        **extra,
    }
    if subscription is not None and not context.get("append") and "new_count" not in context:
        # The number on the "Нові" tab
        context["new_count"] = _search(subscription, filters, view=VIEW_NEW, limit=0).total
    return context


# Hero radar animation. The beam turns once per SWEEP_SECONDS; at rotation 0 its
# leading edge points to BEAM_BEARING (degrees clockwise from 12 o'clock).
SWEEP_SECONDS = 6.0
BEAM_BEARING = 45.0
TRAIL_DEGREES = 100.0  # long enough for a blip to fade while still inside the trail
TRAIL_SLICES = 24
TRAIL_MAX_OPACITY = 0.3


def _polar(cx: float, cy: float, r: float, bearing: float) -> tuple[float, float]:
    rad = math.radians(bearing)
    return cx + r * math.sin(rad), cy - r * math.cos(rad)


def hit_delay(bearing: float) -> float:
    """Seconds after page load when the beam first passes `bearing`."""
    return ((bearing - BEAM_BEARING) % 360) / 360 * SWEEP_SECONDS


def radar_trail(size=440, radius=210) -> list[dict]:
    """
    The green trail behind the beam as thin wedges, faint at the tail and bright at
    the beam (SVG has no conic gradient). Blurred in the template, so the slices
    blend into one smooth fade.
    """
    centre = size / 2
    step = TRAIL_DEGREES / TRAIL_SLICES
    slices = []
    for i in range(TRAIL_SLICES):
        start = BEAM_BEARING - TRAIL_DEGREES + i * step
        end = start + step + 0.6  # slight overlap: no hairline seams between slices
        x1, y1 = _polar(centre, centre, radius, start)
        x2, y2 = _polar(centre, centre, radius, end)
        slices.append(
            {
                "d": f"M{centre:.1f} {centre:.1f} L{x1:.1f} {y1:.1f} "
                f"A{radius} {radius} 0 0 1 {x2:.1f} {y2:.1f} Z",
                "opacity": f"{TRAIL_MAX_OPACITY * ((i + 1) / TRAIL_SLICES) ** 1.8:.3f}",
            }
        )
    return slices


def radar_blips(vacancies, *, now=None, size=440) -> list[dict]:
    """
    Blips for the hero radar, newest first. Each next blip turns by the golden
    angle and sits a bit further out, so they spread evenly, never overlap, and
    the freshest are closest to the centre. The two newest get a label like
    "Python · 2 хв". Each blip's animation is delayed until the beam reaches it,
    so it lights up exactly as the beam passes and fades inside the trail.
    Numbers are strings, so Ukrainian number formatting can never turn "86.7"
    into "86,7" inside SVG attributes.
    """
    from .templatetags.radar_ui import ago_short

    now = now or timezone.now()
    centre, inner, outer = size / 2, size * 0.2, size * 0.44
    shown = list(vacancies[:HERO_BLIPS])
    blips = []
    for index, vacancy in enumerate(shown):
        spread = (index / max(len(shown) - 1, 1)) ** 0.8
        radius = inner + spread * (outer - inner)
        bearing = (45 + index * 137.508) % 360  # golden angle; the first one upper right
        x, y = _polar(centre, centre, radius, bearing)
        blip = {
            "x": f"{x:.1f}",
            "y": f"{y:.1f}",
            "r": 6 if index == 0 else max(5 - index // 2, 3),
            "opacity": f"{max(1 - index * 0.11, 0.35):.2f}",
            "ring": index == 0,
            "delay": f"{hit_delay(bearing):.2f}",
        }
        if index < 2:
            category = vacancy.categories[0] if vacancy.categories else "DOU"
            text = f"{category} · {ago_short(vacancy.first_seen_at, now)}"
            width = 20 + len(text) * 7.3
            label_x = x + 14 if x < size * 0.62 else x - 14 - width
            blip["label"] = {
                "text": text,
                "x": f"{label_x:.1f}",
                "y": f"{y - 14:.1f}",
                "w": f"{width:.1f}",
                "tx": f"{label_x + 10:.1f}",
                "ty": f"{y + 5:.1f}",
            }
        blips.append(blip)
    return blips


def _int_param(request, name: str, *, default: int) -> int:
    try:
        return max(int(request.GET.get(name, default)), 0)
    except (TypeError, ValueError):
        return default


# --- pages --------------------------------------------------------------------------


def _cookie_radar(request) -> Subscription | None:
    token = request.COOKIES.get(TOKEN_COOKIE)
    return Subscription.objects.filter(token=token).first() if token else None


@require_GET
def home(request):
    context = {
        "existing": _cookie_radar(request),
        "blips": radar_blips(fresh_feed(limit=HERO_BLIPS)),
        "trail": radar_trail(),
        "sweep_seconds": f"{SWEEP_SECONDS:g}",
    }
    return render(request, "subscriptions/home.html", context)


@require_http_methods(["GET", "POST"])
def filters_new(request):
    """Filters page for someone without a radar yet; saving creates one."""
    if request.method == "GET" and "new" not in request.GET:
        if existing := _cookie_radar(request):
            return redirect("subscriptions:detail", existing.token)

    if request.method == "POST":
        form = FiltersForm(request.POST)
        if form.is_valid():
            subscription = form.save()
            response = redirect("subscriptions:detail", subscription.token)
            return _remember_token(response, subscription.token)
    else:
        form = FiltersForm()

    filters = Filters()
    page = search(filters, limit=PREVIEW_SIZE)
    context = {
        "form": form,
        "last_fetched_at": last_fetched_at(),
        **_results_context(None, filters, page, preview=True),
    }
    return render(request, "subscriptions/filters.html", context)


@require_http_methods(["GET", "POST"])
def detail(request, token: str):
    """Filters page of an existing radar: filters, live matches, fresh feed."""
    subscription = get_object_or_404(Subscription, token=token)

    if request.method == "POST" and request.headers.get("HX-Request"):
        return _autosave_filters(request, subscription)
    if request.method == "POST":  # no JS: the whole form, submitted with Enter
        form = FiltersForm(request.POST)
        if form.is_valid():
            form.save(subscription)
            _sync_bot(subscription)
            messages.success(request, "Фільтри збережено.")
            return redirect("subscriptions:detail", subscription.token)
    else:
        form = FiltersForm.for_subscription(subscription)

    filters = Filters.from_subscription(subscription)
    page = _search(subscription, filters)
    context = {
        "form": form,
        "version": _version(subscription),
        "since_id": latest_vacancy_id(),
        "last_fetched_at": last_fetched_at(),
        "feed": fresh_feed(limit=FEED_SIZE),
        **_results_context(subscription, filters, page),
    }
    return _remember_token(render(request, "subscriptions/detail.html", context), token)


@require_GET
def bot(request):
    """Telegram page for someone without a radar: what the bot does, how to start."""
    if existing := _cookie_radar(request):
        return redirect("subscriptions:radar_bot", existing.token)
    return render(request, "subscriptions/bot.html", {"bot_username": _bot_username()})


@require_GET
def radar_bot(request, token: str):
    """Telegram page of a radar: connect, or frequency and quiet hours."""
    subscription = get_object_or_404(Subscription, token=token)
    context = {
        "subscription": subscription,
        "notifications_form": NotificationsForm(instance=subscription),
        **_bot_context(subscription),
    }
    return _remember_token(render(request, "subscriptions/bot.html", context), token)


def _bot_context(subscription: Subscription) -> dict:
    return {
        "bot_username": _bot_username(),
        "deep_link": _deep_link(subscription),
        "next_slot": schedule.next_slot_label(subscription),
        "tz_label": schedule.timezone_label(subscription),
    }


@require_GET
def connect(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    if subscription.is_linked:
        messages.info(request, "Telegram уже підключено.")
        return redirect("subscriptions:radar_bot", token)
    deep_link = _deep_link(subscription)
    qr_svg = ""
    if deep_link:
        import segno

        qr_svg = segno.make(deep_link, error="m").svg_inline(
            scale=5, border=0, dark="#0C1015", light=None
        )
    context = {"subscription": subscription, "qr_svg": qr_svg, **_bot_context(subscription)}
    return render(request, "subscriptions/connect.html", context)


@require_http_methods(["GET", "POST"])
def frequency(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    if request.method == "POST":
        form = NotificationsForm(request.POST, instance=subscription)
        if "skip" in request.POST:
            subscription.frequency = "off"
            subscription.save(update_fields=["frequency", "updated_at"])
            _sync_bot(subscription)
            messages.success(request, "Автосповіщення вимкнено. Перевіряй вручну, коли зручно.")
            return redirect("subscriptions:detail", token)
        if form.is_valid():
            form.save()
            _sync_bot(subscription)
            label = schedule.next_slot_label(subscription)
            text = f"Збережено. Наступне сповіщення {label}." if label else "Збережено."
            messages.success(request, text)
            return redirect("subscriptions:detail", token)
    else:
        form = NotificationsForm(instance=subscription)
    context = {
        "subscription": subscription,
        "notifications_form": form,
        "first_batch": subscription.deliveries.count(),
        **_bot_context(subscription),
    }
    return render(request, "subscriptions/frequency.html", context)


# --- actions ------------------------------------------------------------------------


# Fields the bot settings card can change, one at a time.
CARD_FIELDS = ("frequency", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end")


@require_http_methods(["GET", "POST"])
def notifications(request, token: str):
    """
    Bot settings card on the radar's Telegram page (HTMX).

    GET re-renders it from the database, so a tab you return to (say, after
    changing the frequency in the bot) shows current values.

    POST auto-saves ONE field: the one the user just changed (`changed`, sent by
    the page). Every other field is taken from the database, not from the page.
    Otherwise an old tab would write its stale values back, e.g. switching quiet
    hours there would also revert a frequency just chosen in the bot.
    """
    subscription = get_object_or_404(Subscription, token=token)
    saved = False
    form = NotificationsForm(instance=subscription)
    if request.method == "POST":
        data = _current_card_values(subscription)
        changed = request.POST.get("changed", "")
        if changed in CARD_FIELDS:
            if changed == "quiet_hours_enabled":
                data.pop(changed, None)  # unchecked boxes aren't sent at all
            if changed in request.POST:
                data[changed] = request.POST[changed]
        else:  # no JS: a normal submit of the whole card
            data = request.POST.copy()
        data["timezone"] = request.POST.get("timezone", "")
        form = NotificationsForm(data, instance=subscription)
        saved = form.is_valid()
        if saved:
            form.save()
            _sync_bot(subscription)
            form = NotificationsForm(instance=subscription)
    context = {
        "subscription": subscription,
        "notifications_form": form,
        "saved": saved,
        **_bot_context(subscription),
    }
    return render(request, "subscriptions/_bot_settings.html", context)


def _version(subscription: Subscription) -> str:
    """Changes whenever the radar is saved, here or in the bot."""
    return str(int(subscription.updated_at.timestamp() * 1000))


def _current_filter_values(request, subscription: Subscription):
    """
    Form data for an auto-save: the ONE field the user just changed (`changed`,
    sent by the page) from the request, everything else from the database. A tab
    left open while you edited words in the bot then can't write its stale words
    back when you tick a category. Reset (or an unknown field) sends the whole form.
    """
    changed = request.POST.get("changed", "")
    if changed not in FILTER_FIELDS:
        return request.POST
    known = {value for value, _ in category_choices()}
    data = QueryDict(mutable=True)
    data["include_keywords"] = ", ".join(subscription.include_keywords)
    data["exclude_keywords"] = ", ".join(subscription.exclude_keywords)
    data.setlist("categories", [c for c in subscription.categories if c in known])
    if subscription.remote_only:
        data["remote_only"] = "on"
    data.pop(changed, None)  # an unticked checkbox isn't sent at all
    if changed == "categories":
        data.setlist("categories", request.POST.getlist("categories"))
    elif changed in request.POST:
        data[changed] = request.POST[changed]
    return data


def _autosave_filters(request, subscription: Subscription):
    form = FiltersForm(_current_filter_values(request, subscription))
    if not form.is_valid():
        context = {"errors": form.errors, "fragment": True, "save_status": "error"}
        return render(request, "subscriptions/_results.html", context)
    form.save(subscription)
    _sync_bot(subscription)
    return _render_results(request, subscription, view=VIEW_ALL, save_status="saved")


def _render_results(request, subscription, *, view, offset=0, **extra):
    filters = Filters.from_subscription(subscription)
    page = _search(subscription, filters, view=view, offset=offset)
    extra.update(view=view, append=offset > 0, fragment=True)
    if offset == 0:
        # Reloading the list resets the toast's "since" cursor, and the toast has
        # done its job: the list now includes those vacancies.
        extra.update(
            since_id=latest_vacancy_id(),
            shown=0,
            clear_toast=True,
            last_fetched_at=last_fetched_at(),
        )
    return render(
        request,
        "subscriptions/_results.html",
        _results_context(subscription, filters, page, **extra),
    )


@require_GET
def filters_form(request, token: str):
    """
    The filters card again, from the database, plus fresh matches. Requested when
    the tab becomes visible (say, after editing words in the bot); 204 when
    nothing was saved since the page was drawn.
    """
    subscription = get_object_or_404(Subscription, token=token)
    version = _version(subscription)
    if request.GET.get("v") == version:
        return HttpResponse(status=204)
    filters = Filters.from_subscription(subscription)
    context = {
        "form": FiltersForm.for_subscription(subscription),
        "version": version,
        "since_id": latest_vacancy_id(),
        "shown": 0,
        "clear_toast": True,
        "last_fetched_at": last_fetched_at(),
        "fragment": True,
        "oob": True,
        **_results_context(subscription, filters, _search(subscription, filters)),
    }
    return render(request, "subscriptions/_filters_box.html", context)


def _sync_bot(subscription: Subscription) -> None:
    """Edit the settings message in Telegram to match what was saved here."""
    from bot.sync import refresh_settings_message  # bot depends on this app, not vice versa

    refresh_settings_message(subscription)


def _current_card_values(subscription: Subscription) -> dict:
    data = {
        "frequency": subscription.frequency,
        "quiet_hours_start": subscription.quiet_hours_start,
        "quiet_hours_end": subscription.quiet_hours_end,
    }
    if subscription.quiet_hours_enabled:
        data["quiet_hours_enabled"] = "on"
    return data


@require_POST
def unlink(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    subscription.chat_id = None
    subscription.linked_at = None
    subscription.save(update_fields=["chat_id", "linked_at", "updated_at"])
    messages.success(request, "Telegram відключено. Фільтри на сайті лишились як були.")
    return redirect("subscriptions:radar_bot", token)


# --- HTMX fragments -------------------------------------------------------------------


@require_GET
def preview(request):
    """Matches for the filters currently in the form, without saving anything."""
    form = FiltersForm(request.GET)
    subscription = None
    if token := request.GET.get("token"):
        subscription = Subscription.objects.filter(token=token).first()
    if not form.is_valid():
        return render(
            request, "subscriptions/_results.html", {"errors": form.errors, "fragment": True}
        )
    data = form.cleaned_data
    filters = Filters(
        include=tuple(data["include_keywords"]),
        exclude=tuple(data["exclude_keywords"]),
        categories=frozenset(data["categories"]),
        remote_only=data["remote_only"],
    )
    limit = PAGE_SIZE if subscription else PREVIEW_SIZE
    page = search(filters, limit=limit)
    unsaved = subscription is not None and filters != Filters.from_subscription(subscription)
    context = _results_context(
        subscription, filters, page, preview=True, unsaved=unsaved, fragment=True
    )
    return render(request, "subscriptions/_results.html", context)


@require_GET
def results(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    offset = _int_param(request, "offset", default=0)
    return _render_results(request, subscription, view=_view_param(request), offset=offset)


@require_GET
def live(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    since_id = _int_param(request, "since", default=latest_vacancy_id())
    shown = _int_param(request, "shown", default=0)
    new = newer_than(Filters.from_subscription(subscription), since_id)  # since page load
    # Pop the toast only when a fetch brought MORE new matches than already
    # announced; the same count must not pop up again every minute.
    show_toast = settings.NEW_VACANCIES_TOAST and len(new) > shown
    filters = Filters.from_subscription(subscription)
    context = {
        "subscription": subscription,
        "view": _view_param(request),
        "since_id": since_id,
        "arrived": len(new),
        "new_count": _search(subscription, filters, view=VIEW_NEW, limit=0).total,
        "shown": max(shown, len(new)),
        "newest": new[0] if new else None,
        "last_fetched_at": last_fetched_at(),
        "show_toast": show_toast,
    }
    return render(request, "subscriptions/_live.html", context)


@require_GET
def link_status(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    if subscription.is_linked:
        response = HttpResponse(status=204)
        response["HX-Redirect"] = reverse("subscriptions:frequency", args=[token])
        return response
    return render(request, "subscriptions/_link_status.html", {"subscription": subscription})


@require_GET
def next_slot(request, token: str):
    """Preview of the next notification time for the choices currently in the form."""
    subscription = get_object_or_404(Subscription, token=token)
    form = NotificationsForm(request.GET, instance=subscription)
    if form.is_valid():
        draft = form.save(commit=False)  # not saved: just compute the label
        label = schedule.next_slot_label(draft)
    else:
        label = schedule.next_slot_label(subscription)
    return render(
        request,
        "subscriptions/_next_slot.html",
        {"next_slot": label, "tz_label": schedule.timezone_label(subscription)},
    )


@require_GET
def feed(request):
    return render(request, "subscriptions/_feed.html", {"feed": fresh_feed(limit=FEED_SIZE)})
