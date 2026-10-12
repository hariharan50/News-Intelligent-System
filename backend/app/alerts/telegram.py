"""Sending alerts to Telegram.

deliver_pending_telegram() is a sweep (scheduled every few seconds): it picks alerts whose
Telegram status is pending and sends them. Because the work list lives in PostgreSQL, a
worker crash or a Telegram outage delays alerts but never loses them.

Telegram limits bots to about 30 messages per second overall and 1 per second per chat;
we stay under both with small Redis gates. 429 answers carry retry_after, which we honour.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import Alert, DeliveryStatus, TelegramLink
from app.scoring.engine import tier_of
from app.telegram.client import TelegramClient, TelegramError, esc, make_client
from app.telegram.linking import unlink_chat

log = logging.getLogger(__name__)

SWEEP_LOCK = "nis:lock:telegram-sweep"
GLOBAL_PER_SECOND = 25
MAX_INLINE_WAIT_SECONDS = 30
TIER_ICON = {"critical": "🔴", "high": "🟠", "normal": "🔵", "low": "⚪"}


def format_alert(alert: Alert) -> str:
    tier = tier_of(alert.score) or "low"
    head = f"{TIER_ICON[tier]} <b>{tier.upper()} {alert.score}</b>"
    if alert.pinned:
        head += " · 📌 Pinned"
    lines = [head, "", f"<b>{esc(alert.title)}</b>"]
    if alert.source_name:
        lines.append(esc(alert.source_name))
    why = [r["label"] for r in alert.reasons if r.get("kind") != "importance"][:3]
    if why:
        lines += ["", "💡 " + esc(" · ".join(why))]
    lines += ["", f'<a href="{esc(alert.url)}">Read at the source →</a>']
    return "\n".join(lines)


async def _wait_for_send_slot(redis: Redis, chat_id: int) -> None:
    # Per chat: one message per ~1.1 s. (A shared Redis gate, so no in-process event to await.)
    chat_key = f"nis:tg:chat:{chat_id}"
    while True:
        if await redis.set(chat_key, "1", nx=True, px=1100):
            break
        await asyncio.sleep(max(await redis.pttl(chat_key), 50) / 1000)
    # Global: at most GLOBAL_PER_SECOND per wall-clock second.
    while True:
        second = int(time.time())
        key = f"nis:tg:global:{second}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 2)
        if count <= GLOBAL_PER_SECOND:
            return
        await asyncio.sleep(1 - (time.time() % 1) + 0.01)


async def send_alert(
    alert_id,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    tg: TelegramClient,
) -> str:
    """Try to deliver one pending alert. Returns the resulting status."""
    settings = get_settings()
    async with session_factory() as db:
        alert = await db.get(Alert, alert_id)
        if alert is None or alert.telegram_status != DeliveryStatus.PENDING:
            return "noop"
        link = await db.get(TelegramLink, alert.user_id)
        text = format_alert(alert)

    if link is None:
        status, error = DeliveryStatus.SKIPPED, "Telegram not connected"
    else:
        status, error = DeliveryStatus.FAILED, None
        for attempt in range(1, settings.telegram_max_attempts + 1):
            await _wait_for_send_slot(redis, link.chat_id)
            try:
                await tg.send_message(link.chat_id, text)
                status, error = DeliveryStatus.SENT, None
                break
            except TelegramError as exc:
                error = str(exc)[:500]
                if exc.chat_unreachable:
                    async with session_factory() as db, db.begin():
                        await unlink_chat(db, link.chat_id)
                    log.info("telegram chat unreachable; unlinked user %s", alert.user_id)
                    break
                if not exc.retryable or attempt == settings.telegram_max_attempts:
                    break
                await asyncio.sleep(min(exc.retry_after or 2**attempt, MAX_INLINE_WAIT_SECONDS))

    async with session_factory() as db, db.begin():
        row = await db.get(Alert, alert_id, with_for_update=True)
        if row is not None:
            row.telegram_status = status
            row.telegram_error = error
            row.telegram_attempts += 1
            if status == DeliveryStatus.SENT:
                row.telegram_sent_at = datetime.now(UTC)
    if status != DeliveryStatus.SENT:
        log.warning("telegram alert %s not sent: %s", alert_id, error)
    return status


async def deliver_pending_telegram(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: Redis,
    http: httpx.AsyncClient,
    limit: int = 100,
) -> int:
    """Send every pending Telegram alert, oldest first. One sweep runs at a time."""
    if not get_settings().telegram_configured:
        return 0
    if not await redis.set(SWEEP_LOCK, "1", nx=True, ex=120):
        return 0
    try:
        tg = make_client(http)
        async with session_factory() as db:
            pending = list(
                await db.scalars(
                    select(Alert.id)
                    .where(Alert.telegram_status == DeliveryStatus.PENDING)
                    .order_by(Alert.created_at)
                    .limit(limit)
                )
            )
        for alert_id in pending:
            await send_alert(alert_id, session_factory=session_factory, redis=redis, tg=tg)
        return len(pending)
    finally:
        await redis.delete(SWEEP_LOCK)
