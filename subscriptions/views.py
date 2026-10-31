"""
Website. Pages only read from our DB; scraping happens in background jobs.

HTMX endpoints return HTML fragments:
- results/   : the results list (offset=0) or the next page ("Показати ще");
- new-count/ : the "N нових вакансій" banner, polled every 60 s;
- feed/      : the "Свіжі вакансії" block, refreshed every few minutes.
"""

import math
import random

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from radar.matching import Filters
from radar.search import fresh_feed, latest_vacancy_id, newer_than, search

from .forms import FiltersForm
from .models import Delivery, Subscription

TOKEN_COOKIE = "jr_token"
TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
PAGE_SIZE = 20
FEED_SIZE = 8
SCOPE_BLIPS = 14


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


def _bot_link() -> str:
    username = settings.TELEGRAM_BOT_USERNAME
    return f"https://t.me/{username}" if username else ""


def _telegram_link(subscription: Subscription) -> str:
    """Deep link: opening it sends `/start <token>` to the bot."""
    bot = _bot_link()
    return f"{bot}?start={subscription.token}" if bot else ""


def _delivered_ids(subscription: Subscription, vacancies) -> set[int]:
    if not subscription.chat_id or not vacancies:
        return set()
    return set(
        Delivery.objects.filter(
            subscription=subscription, vacancy_id__in=[v.pk for v in vacancies]
        ).values_list("vacancy_id", flat=True)
    )


def _show_categories(subscription: Subscription) -> bool:
    """Category tags are noise when the user filters by exactly one category."""
    return len(subscription.categories) != 1


def scope_blips(vacancies, *, now=None) -> list[dict]:
    """
    Radar blips for the scope: one per vacancy. Angle comes from the id (golden
    angle, so blips spread evenly and stay put between reloads); distance from
    the centre grows with age, so fresh vacancies sit close to the centre.
    Coordinates are in a 0..200 SVG viewBox.
    """
    now = now or timezone.now()
    blips = []
    for vacancy in vacancies[:SCOPE_BLIPS]:
        hours = max((now - vacancy.first_seen_at).total_seconds() / 3600, 0)
        radius = 18 + min(hours / 72, 1) * 70  # 18..88 px
        angle = math.radians((vacancy.pk * 137.508) % 360)
        blips.append(
            {
                "x": f"{100 + radius * math.cos(angle):.1f}",  # str: immune to locale formatting
                "y": f"{100 + radius * math.sin(angle):.1f}",
                "fresh": hours < 24,
                "delay": f"{random.Random(vacancy.pk).uniform(0, 6):.2f}",
            }
        )
    return blips


@require_http_methods(["GET", "POST"])
def home(request):
    if request.method == "POST":
        form = FiltersForm(request.POST)
        if form.is_valid():
            subscription = form.save()
            return _remember_token(
                redirect("subscriptions:detail", subscription.token), subscription.token
            )
    else:
        form = FiltersForm()

    feed = fresh_feed(limit=FEED_SIZE)
    existing = None
    token = request.COOKIES.get(TOKEN_COOKIE)
    if token:
        existing = Subscription.objects.filter(token=token).first()
    return render(
        request,
        "subscriptions/home.html",
        {"form": form, "feed": feed, "existing": existing, "blips": scope_blips(feed)},
    )


@require_http_methods(["GET", "POST"])
def detail(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)

    if request.method == "POST":
        form = FiltersForm(request.POST)
        if form.is_valid():
            form.save(subscription)
            messages.success(request, "Фільтри збережено.")
            return redirect("subscriptions:detail", subscription.token)
    else:
        form = FiltersForm.for_subscription(subscription)

    page = search(Filters.from_subscription(subscription), limit=PAGE_SIZE)
    context = {
        "subscription": subscription,
        "form": form,
        "page": page,
        "seen_ids": _delivered_ids(subscription, page.vacancies),
        "show_categories": _show_categories(subscription),
        "since_id": latest_vacancy_id(),
        "feed": fresh_feed(limit=FEED_SIZE),
        "telegram_link": _telegram_link(subscription),
        "bot_link": _bot_link(),
        "blips": scope_blips(page.vacancies),
        "share_url": request.build_absolute_uri(),
    }
    return _remember_token(render(request, "subscriptions/detail.html", context), token)


@require_GET
def results(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    offset = _int_param(request, "offset", default=0)
    page = search(Filters.from_subscription(subscription), offset=offset, limit=PAGE_SIZE)
    context = {
        "subscription": subscription,
        "page": page,
        "seen_ids": _delivered_ids(subscription, page.vacancies),
        "show_categories": _show_categories(subscription),
        # The first page also resets the "new vacancies" banner via an OOB swap.
        "since_id": latest_vacancy_id() if offset == 0 else None,
        "append": offset > 0,
    }
    return render(request, "subscriptions/_results.html", context)


@require_GET
def new_count(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    since_id = _int_param(request, "since", default=latest_vacancy_id())
    count = len(newer_than(Filters.from_subscription(subscription), since_id))
    return render(
        request,
        "subscriptions/_banner.html",
        {"subscription": subscription, "since_id": since_id, "count": count},
    )


@require_GET
def feed(request):
    return render(request, "subscriptions/_feed.html", {"feed": fresh_feed(limit=FEED_SIZE)})


def _int_param(request, name: str, *, default: int) -> int:
    try:
        return max(int(request.GET.get(name, default)), 0)
    except (TypeError, ValueError):
        return default
