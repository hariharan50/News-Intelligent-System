"""Alerts inbox, notification settings, Telegram connection, and story feedback."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update

from app.alerts.telegram import format_alert
from app.auth.deps import CurrentUserDep
from app.billing.plans import PlanLimitError, entitlements
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.db.session import SessionDep
from app.feedback.service import record_feedback, reset_learning
from app.ingestion import http
from app.models import Alert, NotificationSettings, Verdict
from app.profiles import service as profiles
from app.scoring.engine import tier_of
from app.telegram.client import TelegramError, make_client
from app.telegram.linking import create_link_token, deep_link, link_for_user

router = APIRouter(tags=["alerts"])

TEST_MESSAGE_COOLDOWN_SECONDS = 30


# --------------------------------------------------------------------------- alerts inbox


class AlertOut(BaseModel):
    id: uuid.UUID
    story_id: uuid.UUID
    profile_id: uuid.UUID
    title: str
    url: str
    source: str
    score: int
    tier: str | None
    pinned: bool
    reasons: list[dict]
    created_at: datetime
    read: bool
    telegram_status: str

    @classmethod
    def of(cls, a: Alert) -> "AlertOut":
        return cls(
            id=a.id, story_id=a.story_id, profile_id=a.profile_id, title=a.title, url=a.url,
            source=a.source_name, score=a.score, tier=tier_of(a.score), pinned=a.pinned,
            reasons=a.reasons, created_at=a.created_at, read=a.read_at is not None,
            telegram_status=a.telegram_status,
        )  # fmt: skip


class AlertsPage(BaseModel):
    total: int
    unread: int
    next_offset: int | None
    items: list[AlertOut]


@router.get("/alerts", response_model=AlertsPage)
async def list_alerts(
    current: CurrentUserDep,
    db: SessionDep,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> AlertsPage:
    base = select(Alert).where(Alert.user_id == current.user_id)
    total, unread = (
        await db.execute(
            select(func.count(), func.count().filter(Alert.read_at.is_(None))).where(
                Alert.user_id == current.user_id
            )
        )
    ).one()
    if unread_only:
        base = base.where(Alert.read_at.is_(None))
    shown = unread if unread_only else total
    rows = await db.scalars(base.order_by(Alert.created_at.desc()).limit(limit).offset(offset))
    return AlertsPage(
        total=shown,
        unread=unread,
        next_offset=offset + limit if offset + limit < shown else None,
        items=[AlertOut.of(a) for a in rows],
    )


@router.get("/alerts/unread-count")
async def unread_count(current: CurrentUserDep, db: SessionDep) -> dict:
    count = await db.scalar(
        select(func.count()).where(Alert.user_id == current.user_id, Alert.read_at.is_(None))
    )
    return {"unread": count}


@router.put("/alerts/{alert_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_alert(alert_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    await db.execute(
        update(Alert)
        .where(Alert.id == alert_id, Alert.user_id == current.user_id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()


@router.post("/alerts/read-all")
async def read_all(current: CurrentUserDep, db: SessionDep) -> dict:
    result = await db.execute(
        update(Alert)
        .where(Alert.user_id == current.user_id, Alert.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"marked": result.rowcount}


# --------------------------------------------------------------------------- notifications


class TelegramStatus(BaseModel):
    configured: bool  # does this server have a bot?
    bot_username: str | None
    connected: bool
    username: str | None
    first_name: str | None
    linked_at: datetime | None


class NotificationsOut(BaseModel):
    telegram_enabled: bool
    quiet_start: int | None
    quiet_end: int | None
    timezone: str
    telegram: TelegramStatus


class NotificationsIn(BaseModel):
    telegram_enabled: bool = True
    quiet_start: int | None = Field(default=None, ge=0, le=1439)
    quiet_end: int | None = Field(default=None, ge=0, le=1439)
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown timezone") from None
        return value


async def _notifications(db: SessionDep, user_id: uuid.UUID) -> NotificationsOut:
    settings = get_settings()
    prefs = await db.get(NotificationSettings, user_id)
    link = await link_for_user(db, user_id)
    return NotificationsOut(
        telegram_enabled=prefs.telegram_enabled if prefs else True,
        quiet_start=prefs.quiet_start if prefs else None,
        quiet_end=prefs.quiet_end if prefs else None,
        timezone=prefs.timezone if prefs else "Asia/Kolkata",
        telegram=TelegramStatus(
            configured=settings.telegram_configured,
            bot_username=settings.telegram_bot_username if settings.telegram_configured else None,
            connected=link is not None,
            username=link.username if link else None,
            first_name=link.first_name if link else None,
            linked_at=link.linked_at if link else None,
        ),
    )


@router.get("/notifications", response_model=NotificationsOut)
async def get_notifications(current: CurrentUserDep, db: SessionDep) -> NotificationsOut:
    return await _notifications(db, current.user_id)


@router.put("/notifications", response_model=NotificationsOut)
async def put_notifications(
    body: NotificationsIn, current: CurrentUserDep, db: SessionDep
) -> NotificationsOut:
    if (body.quiet_start is None) != (body.quiet_end is None):
        raise HTTPException(
            422, detail={"code": "quiet_hours", "message": "Set both a start and an end."}
        )
    prefs = await db.get(NotificationSettings, current.user_id)
    if prefs is None:
        prefs = NotificationSettings(user_id=current.user_id)
        db.add(prefs)
    prefs.telegram_enabled = body.telegram_enabled
    prefs.quiet_start = body.quiet_start
    prefs.quiet_end = body.quiet_end
    prefs.timezone = body.timezone
    await db.commit()
    return await _notifications(db, current.user_id)


@router.post("/notifications/telegram/link")
async def telegram_link(current: CurrentUserDep, db: SessionDep) -> dict:
    """A one-time t.me link (15 minutes). Opening it in Telegram connects that chat."""
    settings = get_settings()
    if not settings.telegram_configured:
        raise HTTPException(
            503, detail={"code": "telegram_not_configured", "message": "Telegram isn't set up yet."}
        )
    if not (await entitlements(db, current.user_id)).telegram:
        raise PlanLimitError("telegram_plan", "Telegram alerts are part of Pro.")
    token = await create_link_token(redis_client, current.user_id)
    return {"url": deep_link(token), "expires_in": settings.telegram_link_ttl_seconds}


@router.delete("/notifications/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def telegram_unlink(current: CurrentUserDep, db: SessionDep) -> None:
    link = await link_for_user(db, current.user_id)
    if link is not None:
        await db.delete(link)
        await db.commit()


@router.post("/notifications/telegram/test")
async def telegram_test(current: CurrentUserDep, db: SessionDep) -> dict:
    link = await link_for_user(db, current.user_id)
    if link is None:
        raise HTTPException(
            409, detail={"code": "telegram_not_connected", "message": "Connect Telegram first."}
        )
    cooldown = f"nis:tg:test:{current.user_id}"
    if not await redis_client.set(cooldown, "1", nx=True, ex=TEST_MESSAGE_COOLDOWN_SECONDS):
        raise HTTPException(
            429, detail={"code": "slow_down", "message": "Wait a few seconds and try again."}
        )
    sample = Alert(
        title="Test alert: this is how News Intelligence alerts look",
        url="https://example.com",
        source_name="News Intelligence",
        score=95,
        pinned=False,
        reasons=[{"kind": "pin", "label": "Your Telegram connection works", "weight": None}],
    )
    try:
        await make_client(http.get_client()).send_message(link.chat_id, format_alert(sample))
    except TelegramError as exc:
        raise HTTPException(
            502, detail={"code": "telegram_error", "message": f"Telegram said: {exc}"}
        ) from None
    return {"sent": True}


# --------------------------------------------------------------------------- feedback


class FeedbackIn(BaseModel):
    profile_id: uuid.UUID
    verdict: Literal["relevant", "not_relevant"]


class FeedbackOut(BaseModel):
    verdict: str
    adjustments: list[dict]
    suggestion: dict | None


@router.post("/stories/{story_id}/feedback", response_model=FeedbackOut)
async def feedback(
    story_id: uuid.UUID, body: FeedbackIn, current: CurrentUserDep, db: SessionDep
) -> FeedbackOut:
    """Tune the profile from one story. Matching rule weights move one step."""
    profile = await profiles.get_owned_profile(
        db, current.user_id, body.profile_id, for_update=True
    )
    result = await record_feedback(db, current.user_id, profile, story_id, Verdict(body.verdict))
    await db.commit()
    return FeedbackOut(
        verdict=result.verdict,
        adjustments=[
            {"label": a.label, "from": a.before, "to": a.after} for a in result.adjustments
        ],
        suggestion=result.suggestion,
    )


@router.post("/profiles/{profile_id}/reset-learning")
async def reset_profile_learning(
    profile_id: uuid.UUID, current: CurrentUserDep, db: SessionDep
) -> dict:
    profile = await profiles.get_owned_profile(db, current.user_id, profile_id, for_update=True)
    changed = await reset_learning(db, profile)
    await db.commit()
    return {"reset": changed}
