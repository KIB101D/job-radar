"""Installable app: manifests, the service worker and the offline page (subscriptions/pwa.py)."""

import json
import re
import struct
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.urls import reverse

from subscriptions.models import Subscription

pytestmark = pytest.mark.django_db

ICONS = Path(settings.BASE_DIR) / "static" / "img" / "icons"


@pytest.fixture
def subscription():
    return Subscription.objects.create(include_keywords=["python"])


def manifest_of(response) -> dict:
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    return json.loads(response.content)


def linked_manifest(html: str) -> str:
    return re.search(r'<link rel="manifest" href="([^"]+)"', html).group(1)


def test_general_manifest_starts_where_the_cookie_leads(client):
    data = manifest_of(client.get("/manifest.webmanifest"))

    assert data["start_url"] == "/filters/"  # redirects to the radar from the cookie
    assert data["display"] == "standalone"
    assert data["scope"] == "/" and data["id"] == "/"
    assert data["name"] == "Job Radar"
    purposes = {icon["purpose"] for icon in data["icons"]}
    assert purposes == {"any", "maskable"}
    assert {icon["sizes"] for icon in data["icons"]} >= {"192x192", "512x512"}


def test_radar_manifest_opens_that_radar(client, subscription):
    response = client.get(reverse("subscriptions:radar_manifest", args=[subscription.token]))
    data = manifest_of(response)

    assert data["start_url"] == f"/s/{subscription.token}/"
    radar = f"/s/{subscription.token}/"
    assert [s["url"] for s in data["shortcuts"]] == [radar, f"{radar}bot/"]
    assert data["id"] == "/"  # one app, whichever page it was installed from
    assert response["Cache-Control"].startswith("private")  # carries the private link


def test_unknown_radar_has_no_manifest(client):
    assert client.get("/s/nope/manifest.webmanifest").status_code == 404


def test_pages_link_the_right_manifest(client, subscription):
    assert linked_manifest(client.get("/").content.decode()) == "/manifest.webmanifest"
    assert linked_manifest(client.get("/data/").content.decode()) == "/manifest.webmanifest"
    for name in ("detail", "radar_bot", "radar_data"):
        page = reverse(f"subscriptions:{name}", args=[subscription.token])
        html = client.get(page).content.decode()
        assert linked_manifest(html) == f"/s/{subscription.token}/manifest.webmanifest"


def test_pages_carry_the_ios_tags_and_the_hidden_install_entry(client, subscription):
    html = client.get(reverse("subscriptions:detail", args=[subscription.token])).content.decode()

    assert 'rel="apple-touch-icon"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    # Shown by app.js only when installing is possible.
    assert html.count("data-install ") == 1  # the badge in the filters column only
    assert 'class="install-badge" data-install data-install-button hidden' in html
    assert "data-install-hint" in html


def test_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    body = response.content.decode()

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Cache-Control"] == "no-cache"
    assert response["Service-Worker-Allowed"] == "/"
    precache = json.loads(re.search(r"const PRECACHE = (\[.*?\]);", body).group(1))
    assert "/offline/" in precache
    assert any(path.endswith("app.css") for path in precache)
    assert re.search(r'const VERSION = "[0-9a-f]{12}";', body)


class HashedStorage(StaticFilesStorage):
    """Like production's manifest storage: hashed names, and no entry for ''."""

    def url(self, name):
        if not name:
            raise ValueError("Missing staticfiles manifest entry for ''")
        stem, _, ext = name.rpartition(".")
        return super().url(f"{stem}.abc123.{ext}")


def test_service_worker_works_with_hashed_static_names(client, settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": f"{__name__}.HashedStorage"},
    }

    body = client.get("/sw.js").content.decode()

    assert '"/static/css/app.abc123.css"' in body
    assert 'const STATIC_PREFIX = "/static/";' in body


def test_service_worker_leaves_pages_and_htmx_to_the_network(client):
    body = client.get("/sw.js").content.decode()

    # Only GETs of this origin; pages from the network with the offline page as fallback.
    assert 'request.method !== "GET"' in body
    assert "fetch(request).catch(() => caches.match(OFFLINE_URL))" in body
    # Nothing but static files is put into the cache.
    assert body.count("cache.put(") == 1 and "url.pathname.startsWith(STATIC_PREFIX)" in body


def test_service_worker_version_follows_the_precached_files(client, monkeypatch):
    from subscriptions import pwa

    before = re.search(r'VERSION = "(\w+)"', client.get("/sw.js").content.decode()).group(1)
    monkeypatch.setattr(pwa, "SW_REVISION", "next")
    after = re.search(r'VERSION = "(\w+)"', client.get("/sw.js").content.decode()).group(1)

    assert before != after


def test_offline_page_needs_no_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/offline/")

    assert response.status_code == 200
    assert "Немає з'єднання" in response.content.decode()


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon-192.png", 192),
        ("icon-512.png", 512),
        ("maskable-512.png", 512),
        ("apple-touch-icon.png", 180),
    ],
)
def test_icons_have_the_declared_sizes(name, size):
    header = (ICONS / name).read_bytes()[:24]

    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", header[16:24]) == (size, size)
