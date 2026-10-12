"""Alerts: one per user per event, delivered in the app and on Telegram.

create_alerts() runs right after scoring, for pairs that just crossed a profile's alert line.
  * The same story never alerts a user twice (unique user+story).
  * Several stories about one event (the RBI's rupee move reported by three outlets, or
    split into three cards) alert once: a story whose event type and a named company or
    institution match an alert the user got in the last few hours is grouped, not alerted.
  * In-app delivery is immediate (live event to open browsers). Telegram delivery is marked
    pending and sent by deliver_pending_telegram(), so a crash can't lose an alert.
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.plans import entitlements_for
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.events import publish_to_user
from app.models import (
    Alert,
    DeliveryStatus,
    NotificationSettings,
    Source,
    Story,
    TelegramLink,
)
from app.processing.taxonomy import ENTITIES_BY_SLUG

log = logging.getLogger(__name__)

# Countries are too broad to say two stories are the same event.
_GROUPING_ENTITY_KINDS = {"central_bank", "regulator", "institution", "company", "commodity",
                          "currency", "index"}  # fmt: skip


class AlertCandidate(Protocol):
    profile_id: uuid.UUID
    story_id: uuid.UUID
    user_id: uuid.UUID
    score: int
    pinned: bool
    reasons: tuple[dict, ...]


def event_signature(story: Story) -> tuple[str | None, frozenset[str]]:
    specific = frozenset(
        e
        for e in story.entities
        if (ent := ENTITIES_BY_SLUG.get(e)) is not None and ent.kind in _GROUPING_ENTITY_KINDS
    )
    return story.event_type, specific


def same_event(a: tuple[str | None, frozenset[str]], b: tuple[str | None, frozenset[str]]) -> bool:
    return a[0] is not None and a[0] == b[0] and bool(a[1] & b[1])


def in_quiet_hours(settings: NotificationSettings | None, now: datetime) -> bool:
    if settings is None or settings.quiet_start is None or settings.quiet_end is None:
        return False
    try:
        local = now.astimezone(ZoneInfo(settings.timezone))
    except Exception:
        local = now
    minute = local.hour * 60 + local.minute
    start, end = settings.quiet_start, settings.quiet_end
    if start == end:
        return False
    return start <= minute < end if start < end else minute >= start or minute < end


def alert_event(alert: Alert) -> dict:
    return {
        "type": "alert",
        "alert_id": str(alert.id),
        "profile_id": str(alert.profile_id),
        "story_id": str(alert.story_id),
        "title": alert.title,
        "url": alert.url,
        "source": alert.source_name,
        "score": alert.score,
        "pinned": alert.pinned,
    }


async def create_alerts(
    session_factory: async_sessionmaker[AsyncSession], candidates: list[AlertCandidate]
) -> list[uuid.UUID]:
    """Create alerts for pairs that just crossed an alert line. Returns the new alert ids."""
    if not candidates:
        return []
    settings = get_settings()
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=settings.alert_group_window_hours)
    created: list[Alert] = []

    async with session_factory() as db, db.begin():
        story_ids = {c.story_id for c in candidates}
        user_ids = {c.user_id for c in candidates}
        stories = {s.id: s for s in await db.scalars(select(Story).where(Story.id.in_(story_ids)))}
        source_names = {
            sid: name
            for sid, name in await db.execute(
                select(Source.id, Source.name).where(
                    Source.id.in_({s.lead_source_id for s in stories.values()})
                )
            )
        }

        # What each user was already alerted about recently, as event signatures.
        recent: dict[uuid.UUID, list[tuple]] = defaultdict(list)
        for user_id, story in await db.execute(
            select(Alert.user_id, Story)
            .join(Story, Story.id == Alert.story_id)
            .where(Alert.user_id.in_(user_ids), Alert.created_at >= window_start)
        ):
            recent[user_id].append((story.id, event_signature(story)))

        links = set(
            await db.scalars(select(TelegramLink.user_id).where(TelegramLink.user_id.in_(user_ids)))
        )
        plans = await entitlements_for(db, list(user_ids))
        prefs = {
            p.user_id: p
            for p in await db.scalars(
                select(NotificationSettings).where(NotificationSettings.user_id.in_(user_ids))
            )
        }

        # Highest score first: if one event produced several stories, alert on the best one.
        seen_per_user: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for c in sorted(candidates, key=lambda c: -c.score):
            story = stories.get(c.story_id)
            if story is None or (c.user_id, c.story_id) in seen_per_user:
                continue
            seen_per_user.add((c.user_id, c.story_id))
            signature = event_signature(story)
            if any(sid == story.id or same_event(sig, signature) for sid, sig in recent[c.user_id]):
                log.info("alert grouped with an earlier one: user %s story %s", c.user_id, story.id)
                continue

            pref = prefs.get(c.user_id)
            if (
                c.user_id not in links
                or (pref is not None and not pref.telegram_enabled)
                or not plans[c.user_id].telegram  # Telegram is a paid feature
            ):
                telegram = DeliveryStatus.SKIPPED
            elif in_quiet_hours(pref, now) and not c.pinned:
                telegram = DeliveryStatus.QUIET
            else:
                telegram = DeliveryStatus.PENDING

            values = {
                "user_id": c.user_id,
                "profile_id": c.profile_id,
                "story_id": story.id,
                "score": c.score,
                "title": story.title,
                "url": story.url,
                "source_name": story.lead_publisher or source_names.get(story.lead_source_id, ""),
                "pinned": c.pinned,
                "reasons": list(c.reasons)[:6],
                "telegram_status": telegram,
            }
            alert = await db.scalar(
                pg_insert(Alert).values(values).on_conflict_do_nothing().returning(Alert)
            )
            if alert is not None:
                created.append(alert)
                recent[c.user_id].append((story.id, signature))

    for alert in created:
        await publish_to_user(redis_client, alert.user_id, alert_event(alert))
    if created:
        log.info("created %d alert(s)", len(created))
    return [a.id for a in created]
