"""Connecting a Telegram chat to an account.

1. The signed-in user asks for a link: we store a random one-time token in Redis (hashed,
   15-minute expiry) and return https://t.me/<bot>?start=<token>.
2. Opening it makes Telegram send "/start <token>" to our bot, from the user's chat.
3. The poller consumes the token (single use) and stores user -> chat_id.

The chat id never comes from the browser, so nobody can point alerts at someone else's chat.
"""

import hashlib
import secrets
import uuid

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import TelegramLink


def _key(token: str) -> str:
    return "nis:tglink:" + hashlib.sha256(token.encode()).hexdigest()


async def create_link_token(redis: Redis, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(24)  # [A-Za-z0-9_-], within Telegram's 64-char start limit
    await redis.set(_key(token), str(user_id), ex=get_settings().telegram_link_ttl_seconds)
    return token


def deep_link(token: str) -> str:
    return f"https://t.me/{get_settings().telegram_bot_username}?start={token}"


async def consume_link_token(redis: Redis, token: str) -> uuid.UUID | None:
    value = await redis.getdel(_key(token))
    return uuid.UUID(value) if value else None


async def link_chat(db: AsyncSession, user_id: uuid.UUID, chat: dict) -> TelegramLink:
    chat_id = int(chat["id"])
    # A chat belongs to one account: connecting it here disconnects it elsewhere.
    await db.execute(delete(TelegramLink).where(TelegramLink.chat_id == chat_id))
    link = await db.get(TelegramLink, user_id)
    if link is None:
        link = TelegramLink(user_id=user_id, chat_id=chat_id)
        db.add(link)
    link.chat_id = chat_id
    link.username = (chat.get("username") or "")[:64] or None
    link.first_name = (chat.get("first_name") or "")[:128] or None
    await db.flush()
    return link


async def unlink_chat(db: AsyncSession, chat_id: int) -> bool:
    result = await db.execute(delete(TelegramLink).where(TelegramLink.chat_id == chat_id))
    return result.rowcount > 0


async def link_for_user(db: AsyncSession, user_id: uuid.UUID) -> TelegramLink | None:
    return await db.scalar(select(TelegramLink).where(TelegramLink.user_id == user_id))
