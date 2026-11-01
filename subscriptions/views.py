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
- preview/                matches for unsaved filters (updates as the form changes)
- s/<token>/results/      results list, first page or "Показати ще"
- s/<token>/live/         polled every 60 s: "Нові · N" tab, last-fetch stamp, toast
- s/<token>/link-status/  polled on the connect page until the chat is linked
- s/<token>/next-slot/    "Наступне сповіщення о 18:00" for unsaved frequency choices
"""

import math
import random
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from radar.matching import Filters
from radar.models import Source
from radar.search import fresh_feed, latest_vacancy_id, newer_than, search

from . import schedule
from .forms import FiltersForm, NotificationsForm
from .models import Delivery, Subscription

TOKEN_COOKIE = "jr_token"
TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
PAGE_SIZE = 20
PREVIEW_SIZE = 8
FEED_SIZE = 6
HERO_BLIPS = 9
FRESH_HOURS = 24


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


def _results_context(subscription, filters: Filters, page, **extra) -> dict:
    return {
        "subscription": subscription,
        "filters": filters,
        "page": page,
        "remaining": max(page.total - page.next_offset, 0),
        "seen_ids": _delivered_ids(subscription, page.vacancies),
        "fresh_after": timezone.now() - timedelta(hours=FRESH_HOURS),
        **extra,
    }


def radar_blips(vacancies, *, now=None, size=440) -> list[dict]:
    """
    Blips for the hero radar, newest first. Each next blip turns by the golden
    angle and sits a bit further out, so they spread evenly, never overlap, and
    the freshest are closest to the centre. The two newest get a label like
    "Python · 2 хв". Coordinates are strings, so Ukrainian number formatting
    can never turn "86.7" into "86,7" inside SVG attributes.
    """
    from .templatetags.radar_ui import ago_short

    now = now or timezone.now()
    centre, inner, outer = size / 2, size * 0.2, size * 0.44
    shown = list(vacancies[:HERO_BLIPS])
    blips = []
    for index, vacancy in enumerate(shown):
        spread = (index / max(len(shown) - 1, 1)) ** 0.8
        radius = inner + spread * (outer - inner)
        angle = math.radians(-45 + index * 137.508)  # first blip upper right
        x = centre + radius * math.cos(angle)
        y = centre + radius * math.sin(angle)
        blip = {
            "x": f"{x:.1f}",
            "y": f"{y:.1f}",
            "r": 6 if index == 0 else max(5 - index // 2, 3),
            "opacity": f"{max(1 - index * 0.11, 0.25):.2f}",
            "ring": index == 0,
            "delay": f"{random.Random(vacancy.pk).uniform(0, 6):.2f}",
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

    if request.method == "POST":
        form = FiltersForm(request.POST)
        if form.is_valid():
            form.save(subscription)
            messages.success(request, "Фільтри збережено.")
            return redirect("subscriptions:detail", subscription.token)
    else:
        form = FiltersForm.for_subscription(subscription)

    filters = Filters.from_subscription(subscription)
    page = search(filters, limit=PAGE_SIZE)
    context = {
        "form": form,
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
            messages.success(request, "Автосповіщення вимкнено. Перевіряй вручну, коли зручно.")
            return redirect("subscriptions:detail", token)
        if form.is_valid():
            form.save()
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
            form = NotificationsForm(instance=subscription)
    context = {
        "subscription": subscription,
        "notifications_form": form,
        "saved": saved,
        **_bot_context(subscription),
    }
    return render(request, "subscriptions/_bot_settings.html", context)


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
    filters = Filters.from_subscription(subscription)
    page = search(filters, offset=offset, limit=PAGE_SIZE)
    extra = {"append": offset > 0, "fragment": True}
    if offset == 0:
        # Reloading the list resets the "Нові · N" counter to a fresh cursor.
        extra.update(since_id=latest_vacancy_id(), new_count=0, last_fetched_at=last_fetched_at())
    return render(
        request,
        "subscriptions/_results.html",
        _results_context(subscription, filters, page, **extra),
    )


@require_GET
def live(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    since_id = _int_param(request, "since", default=latest_vacancy_id())
    new = newer_than(Filters.from_subscription(subscription), since_id)
    context = {
        "subscription": subscription,
        "since_id": since_id,
        "new_count": len(new),
        "newest": new[0] if new else None,
        "last_fetched_at": last_fetched_at(),
        "toast_enabled": settings.NEW_VACANCIES_TOAST,
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
