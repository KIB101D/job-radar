from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _load


@pytest.fixture
def python_source(db):
    from radar.models import Source

    # Seeded by the data migration.
    return Source.objects.get(category="Python")


@pytest.fixture
def no_sleep():
    """A sleep() replacement that records delays instead of waiting."""
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    _sleep.calls = calls
    return _sleep


@pytest.fixture
def make_vacancy(db):
    """Create a Vacancy with sensible defaults; `categories` attaches feeds."""
    from datetime import timedelta

    from django.utils import timezone

    from radar.models import Source, Vacancy

    counter = {"n": 0}

    def _make(categories=("Python",), **kwargs):
        counter["n"] += 1
        n = counter["n"]
        now = timezone.now()
        sources = [Source.objects.get(category=c) for c in categories]
        defaults = {
            "guid": f"https://jobs.dou.ua/companies/acme/vacancies/{900000 + n}/",
            "url": f"https://jobs.dou.ua/companies/acme/vacancies/{900000 + n}/",
            "title": f"Vacancy {n}",
            "company": "Acme",
            "description_text": "",
            "location": "Київ",
            "published_at": now - timedelta(minutes=n),
            "first_seen_at": now,
            "last_seen_at": now,
            "source": sources[0],
        }
        defaults.update(kwargs)
        vacancy = Vacancy.objects.create(**defaults)
        vacancy.sources.set(sources)
        return vacancy

    return _make


class FakeTelegram:
    """
    Records Bot API calls instead of sending them. Every call answers ok with a
    growing message_id; `fail_next(method, code, description)` makes one call fail.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 100
        self._failures: dict[str, tuple[int, str]] = {}
        self.updates: list[dict] = []
        self.webhook_url = ""

    def fail_next(self, method: str, code, description: str, parameters=None) -> None:
        self._failures[method] = (
            (code, description, parameters) if parameters else (code, description)
        )

    def handler(self, request):
        import json

        import httpx

        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if method in self._failures:
            code, description, *extra = self._failures.pop(method)
            body = {"ok": False, "error_code": code, "description": description}
            if extra:
                body["parameters"] = extra[0]
            return httpx.Response(200, json=body)
        result = {
            "getMe": {"id": 1, "is_bot": True, "username": "job_radar_test_bot"},
            "getWebhookInfo": {"url": self.webhook_url, "pending_update_count": 0},
            "getUpdates": self.updates,
        }.get(method, True)
        if method in ("sendMessage", "editMessageText"):
            self._next_id += 1
            result = {"message_id": self._next_id, "chat": {"id": payload.get("chat_id")}}
        return httpx.Response(200, json={"ok": True, "result": result})

    # --- helpers for assertions -----------------------------------------------------

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [payload for name, payload in self.calls if name == method]

    def texts(self) -> list[str]:
        return [payload["text"] for payload in self.sent()]

    def last_text(self) -> str:
        return self.texts()[-1]


@pytest.fixture
def tg(monkeypatch):
    """Replace the Telegram client with one whose HTTP goes to FakeTelegram."""
    import httpx

    from bot import digests, handlers, sync, telegram

    fake = FakeTelegram()
    client = telegram.TelegramClient("123:test-token", transport=httpx.MockTransport(fake.handler))
    for module in (telegram, handlers, sync, digests):
        monkeypatch.setattr(module, "get_client", lambda: client)
    from bot.management.commands import run_bot_locally, set_telegram_webhook

    for module in (set_telegram_webhook, run_bot_locally):
        monkeypatch.setattr(module, "get_client", lambda: client)
    return fake
