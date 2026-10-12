"""Minimal Telegram Bot API client (only what we use: getUpdates, sendMessage, getMe)."""

import html
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(
        self, description: str, *, status: int | None = None, retry_after: int | None = None
    ) -> None:
        super().__init__(description)
        self.status = status
        self.retry_after = retry_after

    @property
    def chat_unreachable(self) -> bool:
        """The user blocked the bot or deleted the chat: stop sending to it."""
        return self.status in (400, 403) and any(
            phrase in str(self).lower()
            for phrase in ("blocked", "chat not found", "user is deactivated", "kicked")
        )

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


class NotConfigured(TelegramError):
    def __init__(self) -> None:
        super().__init__("Telegram bot is not configured")


@dataclass
class TelegramClient:
    http: httpx.AsyncClient
    token: str
    api_base: str = "https://api.telegram.org"

    async def call(
        self, method: str, payload: dict[str, Any], *, request_seconds: float = 15
    ) -> Any:
        url = f"{self.api_base}/bot{self.token}/{method}"
        try:
            resp = await self.http.post(url, json=payload, timeout=request_seconds)
        except httpx.HTTPError as exc:
            raise TelegramError(f"network error: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            raise TelegramError(f"HTTP {resp.status_code}", status=resp.status_code) from None
        if not body.get("ok"):
            params = body.get("parameters") or {}
            raise TelegramError(
                body.get("description", f"HTTP {resp.status_code}"),
                status=body.get("error_code", resp.status_code),
                retry_after=params.get("retry_after"),
            )
        return body["result"]

    async def send_message(self, chat_id: int, text: str) -> None:
        await self.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def get_updates(self, offset: int | None, long_poll_seconds: int = 25) -> list[dict]:
        """Long poll: Telegram holds the request open until a message arrives or time runs out."""
        payload: dict[str, Any] = {"timeout": long_poll_seconds, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return await self.call("getUpdates", payload, request_seconds=long_poll_seconds + 10)


def make_client(http: httpx.AsyncClient) -> TelegramClient:
    settings = get_settings()
    if not settings.telegram_configured:
        raise NotConfigured()
    return TelegramClient(
        http=http, token=settings.telegram_bot_token, api_base=settings.telegram_api_base
    )


def esc(value: str) -> str:
    """Escape text for Telegram's HTML parse mode."""
    return html.escape(value, quote=False)
