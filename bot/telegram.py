"""
Minimal Telegram Bot API client on httpx. No bot framework: the handful of
methods we need are plain JSON POSTs.

The bot token is part of every request URL, so errors are re-raised without
the URL (httpx puts it in exception messages) to keep the token out of logs.
"""

import logging
from functools import lru_cache
from typing import Any

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 15.0
# Polling uses long requests; the HTTP timeout must outlast Telegram's wait.
POLL_TIMEOUT_SECONDS = 30

HTML = "HTML"
NO_PREVIEW = {"is_disabled": True}


class TelegramError(Exception):
    def __init__(
        self,
        method: str,
        description: str,
        error_code: int | None = None,
        retry_after: int | None = None,
    ):
        self.method = method
        self.description = description
        self.error_code = error_code
        self.retry_after = retry_after  # set by Telegram on 429 Too Many Requests
        super().__init__(f"{method}: {error_code or ''} {description}".strip())

    @property
    def not_modified(self) -> bool:
        return "message is not modified" in self.description

    @property
    def chat_unavailable(self) -> bool:
        """The user blocked the bot or deleted the chat: stop writing to it."""
        return self.error_code == 403 or "chat not found" in self.description


class TelegramClient:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None):
        if not token:
            raise TelegramError("init", "TELEGRAM_BOT_TOKEN is not set")
        self._base = f"{API_BASE}/bot{token}/"
        self._http = httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport)

    def call(self, method: str, *, http_timeout: float | None = None, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._http.post(
                self._base + method, json=payload, timeout=http_timeout or TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise TelegramError(method, type(exc).__name__) from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError(method, "non-JSON response", response.status_code) from None
        if not data.get("ok"):
            description = data.get("description", "unknown error")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            raise TelegramError(method, description, data.get("error_code"), retry_after)
        return data["result"]

    # --- messages -----------------------------------------------------------------

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=HTML,
            link_preview_options=NO_PREVIEW,
            reply_markup=reply_markup,
        )

    def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None
    ) -> dict | None:
        try:
            return self.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=HTML,
                link_preview_options=NO_PREVIEW,
                reply_markup=reply_markup,
            )
        except TelegramError as exc:
            if exc.not_modified:  # pressing the already selected option
                return None
            raise

    def remove_inline_keyboard(self, chat_id: int, message_id: int) -> None:
        try:
            self.call(
                "editMessageReplyMarkup",
                chat_id=chat_id,
                message_id=message_id,
                reply_markup={"inline_keyboard": []},
            )
        except TelegramError as exc:
            if not exc.not_modified:
                raise

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    # --- setup ----------------------------------------------------------------------

    def get_me(self) -> dict:
        return self.call("getMe")

    def set_webhook(self, url: str, secret_token: str, allowed_updates: list[str]) -> None:
        self.call(
            "setWebhook",
            url=url,
            secret_token=secret_token,
            allowed_updates=allowed_updates,
            drop_pending_updates=True,
        )

    def delete_webhook(self) -> None:
        self.call("deleteWebhook", drop_pending_updates=False)

    def get_webhook_info(self) -> dict:
        return self.call("getWebhookInfo")

    def set_my_commands(self, commands: list[dict]) -> None:
        self.call("setMyCommands", commands=commands)

    def get_updates(self, offset: int | None, allowed_updates: list[str]) -> list[dict]:
        return self.call(
            "getUpdates",
            http_timeout=POLL_TIMEOUT_SECONDS + 10,
            timeout=POLL_TIMEOUT_SECONDS,  # Telegram's long-poll wait
            offset=offset,
            allowed_updates=allowed_updates,
        )


@lru_cache(maxsize=1)
def get_client() -> TelegramClient:
    """The shared client. Tests replace it with one on a mock transport."""
    return TelegramClient(settings.TELEGRAM_BOT_TOKEN)
