"""Administration: pipeline overview, news sources, users, AI. Admins only."""

import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select

from app.ai import service as ai
from app.auth.deps import CurrentUserDep, client_info
from app.auth.service import revoke_all_sessions
from app.billing.plans import all_plans, entitlements_for
from app.cache.redis import HEARTBEAT_KEY, redis_client
from app.core.config import get_settings
from app.core.net import PUBLIC_ONLY_HOOKS, validate_feed_url
from app.db.session import SessionDep
from app.ingestion.adapters import FetchError, SourceRef, get_adapter
from app.ingestion.service import REGION_TIMEZONES
from app.models import (
    AdminAuditLog,
    AiUsageDaily,
    Alert,
    Article,
    Payment,
    Profile,
    Source,
    SourceFetch,
    SourceKind,
    Story,
    StorySummary,
    TelegramLink,
    User,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@dataclass(frozen=True, slots=True)
class AdminActor:
    """The administrator making a request: who, from where (for the audit log)."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    email: str
    ip_address: str | None


async def require_admin(current: CurrentUserDep, db: SessionDep, request: Request) -> AdminActor:
    row = (
        await db.execute(
            select(User.is_admin, User.email, User.totp_enabled_at).where(
                User.id == current.user_id
            )
        )
    ).first()
    if row is None or not row.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "admin_only", "message": "Only administrators can do that."},
        )
    if get_settings().admin_needs_2fa and row.totp_enabled_at is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "admin_2fa_required",
                "message": "Turn on two-step sign-in in My account to use the admin panel.",
            },
        )
    return AdminActor(
        current.user_id, current.session_id, row.email, client_info(request).ip_address
    )


AdminDep = Annotated[AdminActor, Depends(require_admin)]


async def audit(
    db: SessionDep,
    actor: AdminActor,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object = None,
    details: dict | None = None,
) -> None:
    """Record an admin action in the caller's transaction (it commits with the change)."""
    db.add(
        AdminAuditLog(
            actor_id=actor.user_id,
            actor_email=actor.email,
            action=action,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            details=details or {},
            ip_address=actor.ip_address,
        )
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, detail={"code": "not_found", "message": f"{what} not found."})


# --------------------------------------------------------------------------- overview


@router.get("/overview")
async def overview(_: AdminDep, db: SessionDep) -> dict:
    now = datetime.now(UTC)
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    users_total, users_active_7d = (
        await db.execute(select(func.count(), func.count().filter(User.last_login_at >= week)))
    ).one()
    source_status = dict(
        (
            await db.execute(
                select(Source.status, func.count()).where(Source.enabled).group_by(Source.status)
            )
        ).all()
    )
    telegram_counts = dict(
        (
            await db.execute(
                select(Alert.telegram_status, func.count())
                .where(Alert.created_at >= day)
                .group_by(Alert.telegram_status)
            )
        ).all()
    )
    heartbeat = await redis_client.get(HEARTBEAT_KEY)
    return {
        "users": {
            "total": users_total,
            "active_7d": users_active_7d,
            "telegram_connected": await db.scalar(select(func.count()).select_from(TelegramLink)),
        },
        "news_24h": {
            "articles": await db.scalar(select(func.count()).where(Article.fetched_at >= day)),
            "stories": await db.scalar(select(func.count()).where(Story.created_at >= day)),
        },
        "alerts_24h": {
            "total": sum(telegram_counts.values()),
            "telegram": telegram_counts,
        },
        "sources": {
            "enabled": sum(source_status.values()),
            "disabled": await db.scalar(select(func.count()).where(Source.enabled.is_(False))),
            "by_status": source_status,
        },
        "pipeline_heartbeat_age_seconds": round(time.time() - float(heartbeat), 1)
        if heartbeat
        else None,
        "telegram_configured": get_settings().telegram_configured,
        "billing": await _billing_overview(db, now),
        "ai": {
            "configured": get_settings().ai_configured,
            "enabled": await ai.is_enabled(redis_client),
            "spent_today_usd": float(await ai.spent_today(db, features=ai.SUMMARY_FEATURES)),
            "budget_usd": get_settings().ai_daily_budget_usd,
        },
    }


async def _billing_overview(db: SessionDep, now: datetime) -> dict:
    """Who pays for what, and monthly recurring revenue at list prices."""
    user_ids = list(await db.scalars(select(User.id).where(User.is_active)))
    plans = await all_plans(db)
    by_plan: dict[str, int] = {}
    trials = mrr = 0
    for ent in (await entitlements_for(db, user_ids, now)).values():
        if ent.source == "subscription":
            by_plan[ent.plan] = by_plan.get(ent.plan, 0) + 1
            mrr += plans[ent.plan].price_paise if ent.plan in plans else 0
        elif ent.source == "trial":
            trials += 1
    payments_30d = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
            Payment.status == "captured", Payment.created_at >= now - timedelta(days=30)
        )
    )
    return {
        "configured": get_settings().billing_configured,
        "paying": sum(by_plan.values()),
        "by_plan": by_plan,
        "trials": trials,
        "mrr_paise": mrr,
        "collected_30d_paise": payments_30d,
    }


# --------------------------------------------------------------------------- sources


class SourceAdminOut(BaseModel):
    slug: str
    name: str
    url: str
    homepage: str | None
    category: str
    region: str
    licence_class: str
    authority: int
    is_priority: bool
    enabled: bool
    poll_interval_seconds: int
    status: str
    consecutive_failures: int
    last_polled_at: datetime | None
    last_success_at: datetime | None
    last_new_item_at: datetime | None
    next_poll_at: datetime
    last_error: str | None
    notes: str | None
    fetches_24h: int
    errors_24h: int
    new_items_24h: int
    avg_ms_24h: int | None


_SOURCE_FIELDS = [c for c in SourceAdminOut.model_fields if hasattr(Source, c)]


async def _sources_with_stats(db: SessionDep, slug: str | None = None) -> list[SourceAdminOut]:
    """Sources with their last-24-hour fetch statistics. Failing ones first."""
    since = datetime.now(UTC) - timedelta(days=1)
    stats = (
        select(
            SourceFetch.source_id.label("source_id"),
            func.count().label("fetches"),
            func.count().filter(SourceFetch.outcome == "error").label("errors"),
            func.coalesce(func.sum(SourceFetch.items_new), 0).label("new_items"),
            func.avg(SourceFetch.duration_ms).label("avg_ms"),
        )
        .where(SourceFetch.started_at >= since)
        .group_by(SourceFetch.source_id)
        .subquery()
    )
    health_order = case((Source.status == "failing", 0), (Source.status == "degraded", 1), else_=2)
    stmt = (
        select(Source, stats.c.fetches, stats.c.errors, stats.c.new_items, stats.c.avg_ms)
        .outerjoin(stats, stats.c.source_id == Source.id)
        .order_by(Source.enabled.desc(), health_order, Source.name)
    )
    if slug is not None:
        stmt = stmt.where(Source.slug == slug)
    return [
        SourceAdminOut(
            **{field: getattr(source, field) for field in _SOURCE_FIELDS},
            fetches_24h=fetches or 0,
            errors_24h=errors or 0,
            new_items_24h=new_items or 0,
            avg_ms_24h=round(avg_ms) if avg_ms is not None else None,
        )
        for source, fetches, errors, new_items, avg_ms in await db.execute(stmt)
    ]


async def _one(db: SessionDep, slug: str) -> SourceAdminOut:
    found = await _sources_with_stats(db, slug)
    if not found:
        raise _not_found("Source")
    return found[0]


@router.get("/sources", response_model=list[SourceAdminOut])
async def list_sources(_: AdminDep, db: SessionDep) -> list[SourceAdminOut]:
    return await _sources_with_stats(db)


class SourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    url: str = Field(max_length=2000)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    homepage: str | None = Field(default=None, max_length=2000)
    category: Literal["official_india", "official_global", "press_india", "press_global", "other"]
    region: str = Field(default="IN", pattern=r"^[A-Z]{2}$|^GLOBAL$")
    licence_class: Literal["official", "headline_link", "licensed"] = "headline_link"
    authority: int = Field(default=3, ge=1, le=5)
    poll_interval_seconds: int = Field(default=300, ge=15, le=86400)
    is_priority: bool = False
    enabled: bool = True
    notes: str | None = Field(default=None, max_length=1000)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    enabled: bool | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=15, le=86400)
    authority: int | None = Field(default=None, ge=1, le=5)
    is_priority: bool | None = None
    licence_class: Literal["official", "headline_link", "licensed"] | None = None
    notes: str | None = Field(default=None, max_length=1000)


def _bad_url(message: str) -> HTTPException:
    return HTTPException(422, detail={"code": "invalid_url", "message": message})


def _slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")[:63] or "source"


@router.post("/sources", response_model=SourceAdminOut, status_code=201)
async def create_source(body: SourceCreate, actor: AdminDep, db: SessionDep) -> SourceAdminOut:
    try:
        url = str(validate_feed_url(body.url))
        homepage = str(validate_feed_url(body.homepage)) if body.homepage else None
    except ValueError as exc:
        raise _bad_url(str(exc)) from None
    slug = body.slug or _slugify(body.name)
    if await db.scalar(select(Source.id).where(or_(Source.slug == slug, Source.url == url))):
        raise HTTPException(
            409,
            detail={"code": "source_exists", "message": "A source with that slug or URL exists."},
        )
    source = Source(
        slug=slug,
        name=body.name.strip(),
        kind=SourceKind.GOOGLE_NEWS if "news.google.com" in url else SourceKind.RSS,
        url=url,
        homepage=homepage,
        category=body.category,
        region=body.region,
        licence_class=body.licence_class,
        authority=body.authority,
        poll_interval_seconds=body.poll_interval_seconds,
        is_priority=body.is_priority,
        enabled=body.enabled,
        notes=body.notes,
    )
    db.add(source)
    await audit(
        db,
        actor,
        "source.create",
        target_type="source",
        target_id=slug,
        details={"url": url, "enabled": body.enabled},
    )
    await db.commit()
    return await _one(db, slug)


@router.patch("/sources/{slug}", response_model=SourceAdminOut)
async def update_source(
    slug: str, body: SourceUpdate, actor: AdminDep, db: SessionDep
) -> SourceAdminOut:
    source = await db.scalar(select(Source).where(Source.slug == slug).with_for_update())
    if source is None:
        raise _not_found("Source")
    changes = body.model_dump(exclude_unset=True)
    await audit(
        db,
        actor,
        "source.update",
        target_type="source",
        target_id=slug,
        details={k: {"from": getattr(source, k), "to": v} for k, v in changes.items()},
    )
    for field, value in changes.items():
        setattr(source, field, value.strip() if isinstance(value, str) else value)
    if body.enabled:
        source.next_poll_at = datetime.now(UTC)  # re-enabled: poll straight away
    await db.commit()
    return await _one(db, slug)


@router.post("/sources/{slug}/poll-now", status_code=202)
async def poll_now(slug: str, actor: AdminDep, db: SessionDep) -> dict:
    source = await db.scalar(select(Source).where(Source.slug == slug).with_for_update())
    if source is None:
        raise _not_found("Source")
    if not source.enabled:
        raise HTTPException(409, detail={"code": "disabled", "message": "Enable the source first."})
    source.next_poll_at = datetime.now(UTC)
    await audit(db, actor, "source.poll_now", target_type="source", target_id=slug)
    await db.commit()
    return {"queued": True}


class TestIn(BaseModel):
    url: str = Field(max_length=2000)
    region: str = "IN"


@router.post("/sources/test")
async def test_source(body: TestIn, actor: AdminDep, db: SessionDep) -> dict:
    """Fetch a feed once and show what we would store. Nothing is saved."""
    try:
        url = str(validate_feed_url(body.url))
    except ValueError as exc:
        raise _bad_url(str(exc)) from None
    await audit(db, actor, "source.test", target_type="url", target_id=url[:100])
    await db.commit()
    settings = get_settings()
    async with httpx.AsyncClient(
        headers={"User-Agent": settings.ingest_user_agent},
        timeout=settings.ingest_timeout_seconds,
        follow_redirects=True,
        event_hooks=PUBLIC_ONLY_HOOKS,
    ) as client:
        started = time.monotonic()
        try:
            kind = SourceKind.GOOGLE_NEWS if "news.google.com" in url else SourceKind.RSS
            result = await get_adapter(kind).fetch(
                client, SourceRef(url=url, timezone=REGION_TIMEZONES.get(body.region, "UTC"))
            )
        except FetchError as exc:
            return {"ok": False, "error": str(exc), "http_status": exc.http_status, "items": []}
    return {
        "ok": True,
        "http_status": result.http_status,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "item_count": len(result.items),
        "items": [
            {
                "title": i.title,
                "url": i.url,
                "published_at": i.published_at,
                "excerpt": i.excerpt,
                "publisher": i.publisher,
            }
            for i in result.items[:8]
        ],
    }


@router.get("/sources/{slug}/fetches")
async def recent_fetches(slug: str, _: AdminDep, db: SessionDep) -> list[dict]:
    source_id = await db.scalar(select(Source.id).where(Source.slug == slug))
    if source_id is None:
        raise _not_found("Source")
    rows = await db.scalars(
        select(SourceFetch)
        .where(SourceFetch.source_id == source_id)
        .order_by(SourceFetch.started_at.desc())
        .limit(50)
    )
    return [
        {
            "started_at": f.started_at,
            "outcome": f.outcome,
            "http_status": f.http_status,
            "duration_ms": f.duration_ms,
            "items_seen": f.items_seen,
            "items_new": f.items_new,
            "error": f.error,
        }
        for f in rows
    ]


# --------------------------------------------------------------------------- users


class UserAdminOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None
    profiles: int
    telegram_connected: bool
    alerts_7d: int
    plan: str = "free"
    plan_source: str = "free"  # subscription | trial | free


class UserUpdate(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


@router.get("/users")
async def list_users(
    _: AdminDep,
    db: SessionDep,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    week = datetime.now(UTC) - timedelta(days=7)
    profiles = select(func.count()).where(Profile.user_id == User.id).scalar_subquery()
    alerts = (
        select(func.count())
        .where(Alert.user_id == User.id, Alert.created_at >= week)
        .scalar_subquery()
    )
    telegram = select(TelegramLink.user_id).where(TelegramLink.user_id == User.id).exists()
    stmt = select(User, profiles, alerts, telegram)
    count = select(func.count()).select_from(User)
    if q:
        pattern = f"%{q.strip()}%"
        condition = or_(User.email.ilike(pattern), User.full_name.ilike(pattern))
        stmt, count = stmt.where(condition), count.where(condition)
    total = await db.scalar(count)
    rows = (
        await db.execute(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset))
    ).all()
    plans = await entitlements_for(db, [u.id for u, *_ in rows])
    return {
        "total": total,
        "items": [
            UserAdminOut(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                is_active=u.is_active,
                is_admin=u.is_admin,
                created_at=u.created_at,
                last_login_at=u.last_login_at,
                profiles=p,
                telegram_connected=bool(t),
                alerts_7d=a,
                plan=plans[u.id].plan,
                plan_source=plans[u.id].source,
            )
            for u, p, a, t in rows
        ],
    }


@router.patch("/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminDep, db: SessionDep
) -> UserAdminOut:
    if user_id == admin.user_id and (body.is_active is False or body.is_admin is False):
        raise HTTPException(
            409, detail={"code": "self_change", "message": "You can't disable or demote yourself."}
        )
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        raise _not_found("User")
    changes = {
        k: {"from": getattr(user, k), "to": v}
        for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None and getattr(user, k) != v
    }
    if changes:
        await audit(
            db,
            admin,
            "user.update",
            target_type="user",
            target_id=user.id,
            details={"email": user.email, **changes},
        )
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            # Disabling takes effect immediately: every session ends now.
            await revoke_all_sessions(db, redis_client, user.id, reason="disabled_by_admin")
    await db.commit()
    page = await list_users(admin, db, q=user.email, limit=1, offset=0)  # type: ignore[arg-type]
    return page["items"][0]


# --------------------------------------------------------------------------- AI


class AiSwitch(BaseModel):
    enabled: bool


@router.get("/ai")
async def ai_status(_: AdminDep, db: SessionDep) -> dict:
    """AI switch, today's spend against the budget, last 14 days, summary health."""
    s = get_settings()
    today = ai.today_ist()
    days = (
        await db.execute(
            select(
                AiUsageDaily.day,
                func.sum(AiUsageDaily.requests),
                func.sum(AiUsageDaily.failures),
                func.sum(AiUsageDaily.input_tokens),
                func.sum(AiUsageDaily.output_tokens),
                func.sum(AiUsageDaily.cost_usd),
            )
            .where(AiUsageDaily.day > today - timedelta(days=14))
            .group_by(AiUsageDaily.day)
            .order_by(AiUsageDaily.day.desc())
        )
    ).all()
    day_ago = datetime.now(UTC) - timedelta(days=1)
    by_status = dict(
        (
            await db.execute(
                select(StorySummary.status, func.count())
                .where(StorySummary.updated_at >= day_ago)
                .group_by(StorySummary.status)
            )
        ).all()
    )
    recent_errors = (
        await db.execute(
            select(Story.title, StorySummary.last_error, StorySummary.updated_at)
            .join(Story, Story.id == StorySummary.story_id)
            .where(StorySummary.last_error.is_not(None))
            .order_by(StorySummary.updated_at.desc())
            .limit(5)
        )
    ).all()
    spent = await ai.spent_today(db, features=ai.SUMMARY_FEATURES)
    agent_spent = await ai.spent_today(db, features=ai.AGENT_FEATURES)
    return {
        "agent": {
            "model": s.ai_agent_model,
            "budget_usd": s.ai_agent_daily_budget_usd,
            "spent_today_usd": float(agent_spent),
            "questions_limited": s.ai_agent_questions_limited,
            "questions_full": s.ai_agent_questions_full,
        },
        "configured": s.ai_configured,
        "enabled": await ai.is_enabled(redis_client),
        "model": s.ai_summary_model,
        "effort": s.ai_summary_effort,
        "min_score": s.ai_summary_min_score,
        "budget_usd": s.ai_daily_budget_usd,
        "spent_today_usd": float(spent),
        "over_budget": float(spent) >= s.ai_daily_budget_usd,
        "summaries_24h": by_status,
        "days": [
            {
                "day": d.isoformat(),
                "requests": r,
                "failures": f,
                "input_tokens": i,
                "output_tokens": o,
                "cost_usd": float(c),
            }
            for d, r, f, i, o, c in days
        ],
        "recent_errors": [{"title": t, "error": e, "at": at} for t, e, at in recent_errors],
    }


@router.put("/ai")
async def set_ai(body: AiSwitch, admin: AdminDep, db: SessionDep) -> dict:
    """Global AI switch. Off stops new AI work at once; existing summaries stay visible."""
    await ai.set_enabled(redis_client, body.enabled)
    await audit(db, admin, "ai.switch", details={"enabled": body.enabled})
    await db.commit()
    return await ai_status(admin, db)


# --------------------------------------------------------------------------- audit log


@router.get("/audit")
async def audit_log(
    _: AdminDep,
    db: SessionDep,
    action: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Admin actions, newest first."""
    query = select(AdminAuditLog)
    if action:
        query = query.where(AdminAuditLog.action.startswith(action))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.scalars(
        query.order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset)
    )
    return {
        "total": total,
        "items": [
            {
                "id": str(r.id),
                "actor": r.actor_email,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "details": r.details,
                "ip_address": r.ip_address,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }
