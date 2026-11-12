"""Installable web app: the manifest, the service worker and the offline page.

A radar is a private link, so the app has to open *that* link. Pages of a radar link
their own manifest (/s/<token>/manifest.webmanifest) whose start_url is the radar, so
an icon added from it opens the radar even where the installed app doesn't share
cookies with the browser (iOS). Other pages link the general manifest, which starts
at /filters/: that page redirects to the radar from the cookie or offers to make one.

The service worker only makes the app work like one: static files from a cache, and
an offline page instead of the browser's error when there's no connection. Pages and
HTMX requests always go to the network, so nothing stale or private is kept.
"""

import hashlib
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import PrefixNode, static
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import Subscription

APP_NAME = "Job Radar"
BACKGROUND = "#0C1015"  # same as --bg and <meta name="theme-color">

# Bump to make every installed copy drop its cache, even if no static file changed.
SW_REVISION = "1"

# What the offline page needs, cached when the service worker installs.
PRECACHE_STATIC = (
    "css/app.css",
    "js/app.js",
    "js/htmx.min.js",
    "img/favicon.svg",
    "img/icons/icon-192.png",
)


def _icon(name: str, size: int, purpose: str) -> dict:
    return {
        "src": static(f"img/icons/{name}"),
        "sizes": f"{size}x{size}",
        "type": "image/png",
        "purpose": purpose,
    }


def _manifest(start_url: str, shortcuts: list[dict]) -> JsonResponse:
    data = {
        "id": "/",
        "name": APP_NAME,
        "short_name": APP_NAME,
        "description": "Персональний радар IT-вакансій з DOU: збіги наживо і в Telegram.",
        "lang": "uk",
        "dir": "ltr",
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": BACKGROUND,
        "theme_color": BACKGROUND,
        "categories": ["productivity", "business"],
        "icons": [
            _icon("icon-192.png", 192, "any"),
            _icon("icon-512.png", 512, "any"),
            _icon("maskable-512.png", 512, "maskable"),
        ],
        "shortcuts": shortcuts,
    }
    response = JsonResponse(
        data, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False}
    )
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_GET
def manifest(request):
    return _manifest(
        start_url=reverse("subscriptions:filters"),
        shortcuts=[
            {"name": "Фільтри", "url": reverse("subscriptions:filters")},
            {"name": "Telegram-бот", "url": reverse("subscriptions:bot")},
        ],
    )


@require_GET
def radar_manifest(request, token: str):
    subscription = get_object_or_404(Subscription, token=token)
    radar = reverse("subscriptions:detail", args=[subscription.token])
    bot = reverse("subscriptions:radar_bot", args=[subscription.token])
    response = _manifest(
        start_url=radar,
        shortcuts=[{"name": "Збіги", "url": radar}, {"name": "Telegram-бот", "url": bot}],
    )
    # It carries the private link: keep it out of shared caches.
    response["Cache-Control"] = "private, max-age=3600"
    return response


@require_GET
def service_worker(request):
    precache = [reverse("subscriptions:offline"), *(static(path) for path in PRECACHE_STATIC)]
    # Hashed static names (in production) change with the files, so does the version,
    # so a new deploy installs a new worker that replaces the old cache.
    version = hashlib.sha256("|".join([SW_REVISION, *precache]).encode()).hexdigest()[:12]
    response = render(
        request,
        "pwa/sw.js",
        {
            "version": version,
            "precache": json.dumps(precache),
            "offline_url": json.dumps(reverse("subscriptions:offline")),
            # The plain prefix: static("") would ask the hashed-names manifest in production.
            "static_prefix": json.dumps(PrefixNode.handle_simple("STATIC_URL")),
        },
        content_type="application/javascript; charset=utf-8",
    )
    # Browsers check the worker for updates; make sure nothing in between caches it.
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request):
    """Shown by the service worker when a page can't load. No database: it's cached."""
    response = render(request, "pwa/offline.html")
    response["Cache-Control"] = "no-cache"
    return response


def manifest_url(token: str | None) -> str:
    if token:
        return reverse("subscriptions:radar_manifest", args=[token])
    return reverse("subscriptions:manifest")
