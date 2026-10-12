"""Receives messages sent to the bot (long polling) and handles /start, /stop, /status.

Run exactly one: `python -m app.telegram.poller` (the `telegram` compose service).
Telegram rejects two getUpdates consumers on one bot. Without a configured bot token the
process idles, so the container can always be part of the stack.
"""

import asyncio
import logging

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models import TelegramLink, User
from app.telegram.client import TelegramClient, TelegramError, esc, make_client
from app.telegram.linking import consume_link_token, link_chat, unlink_chat

log = logging.getLogger(__name__)

OFFSET_KEY = "nis:telegram:offset"

HELP = (
    "👋 <b>News Intelligence</b> sends your market alerts here.\n\n"
    "To connect, open News Intelligence → <b>Notifications</b> → <b>Connect Telegram</b>.\n"
    "/status: is this chat connected?\n/stop: stop alerts in this chat"
)


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(1, len(name) - 2)}@{domain}"


async def handle_update(
    update: dict,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    tg: TelegramClient,
) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    if not chat or chat.get("type") != "private" or not text:
        return  # only private chats: alerts are personal
    chat_id = int(chat["id"])
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()

    if command == "/start" and argument.strip():
        user_id = await consume_link_token(redis, argument.strip())
        if user_id is None:
            await tg.send_message(
                chat_id,
                "⚠️ This link has expired or was already used.\n"
                "Open News Intelligence → Notifications → Connect Telegram for a new one.",
            )
            return
        async with session_factory() as db, db.begin():
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                return
            await link_chat(db, user_id, chat)
            email = user.email
        await tg.send_message(
            chat_id,
            f"✅ <b>Connected</b> to {esc(mask_email(email))}.\n"
            "You'll get alerts here when a story crosses your alert line.",
        )
        log.info("telegram chat linked for user %s", user_id)
    elif command == "/stop":
        async with session_factory() as db, db.begin():
            removed = await unlink_chat(db, chat_id)
        await tg.send_message(
            chat_id,
            "🔕 Disconnected. You won't get alerts here."
            if removed
            else "This chat isn't connected to an account.",
        )
    elif command == "/status":
        async with session_factory() as db:
            email = await db.scalar(
                select(User.email)
                .join(TelegramLink, TelegramLink.user_id == User.id)
                .where(TelegramLink.chat_id == chat_id)
            )
        await tg.send_message(
            chat_id,
            f"✅ Connected to {esc(mask_email(email))}." if email else "This chat isn't connected.",
        )
    else:
        await tg.send_message(chat_id, HELP)


async def poll_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.telegram_configured:
        log.info("TELEGRAM_BOT_TOKEN/TELEGRAM_BOT_USERNAME not set: Telegram poller idle")
        await asyncio.Event().wait()  # idle until the container is stopped

    backoff = 1.0
    async with httpx.AsyncClient() as http:
        tg = make_client(http)
        log.info("Telegram poller started for @%s", settings.telegram_bot_username)
        while True:
            try:
                raw = await redis_client.get(OFFSET_KEY)
                updates = await tg.get_updates(int(raw) if raw else None)
                for update in updates:
                    try:
                        await handle_update(
                            update, session_factory=SessionLocal, redis=redis_client, tg=tg
                        )
                    except Exception:  # one bad update must not stall the queue
                        log.exception(
                            "failed to handle telegram update %s", update.get("update_id")
                        )
                    await redis_client.set(OFFSET_KEY, update["update_id"] + 1)
                backoff = 1.0
            except TelegramError as exc:
                wait = exc.retry_after or backoff
                log.warning("telegram getUpdates failed (%s); retrying in %ss", exc, wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 60)
            except Exception:
                log.exception("telegram poller error; retrying in %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    from app.core.monitoring import init_monitoring

    init_monitoring("telegram")
    asyncio.run(poll_forever())
