import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import summaries_for
from app.auth.deps import CurrentUserDep
from app.auth.service import within_rate
from app.billing.plans import PlanLimitError, entitlements
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.db.session import SessionDep
from app.ingestion import refresh
from app.models import Article, Profile, Source, Story, StoryScore, StoryUserState
from app.profiles import service as profiles
from app.profiles.service import ProfileError
from app.scoring.engine import TIERS, tier_of

router = APIRouter(tags=["feed"])

TIER_FLOORS = dict(TIERS)
TIER_CEILINGS = {"critical": 101, "high": 80, "normal": 60, "low": 40}
MAX_ARTICLES_PER_ITEM = 10
Tier = Literal["critical", "high", "normal", "low"]


class FeedArticle(BaseModel):
    source: str
    title: str
    url: str
    published_at: datetime


class AiSummary(BaseModel):
    summary: str
    key_facts: list[str]
    affected: list[str]  # AI opinion; labelled as such in the app
    outdated: bool  # more reports arrived since it was written
    updated_at: datetime


class FeedItem(BaseModel):
    story_id: uuid.UUID
    title: str
    url: str
    lead_source: str
    event_type: str | None
    is_routine: bool
    topics: list[str]
    entities: list[str]
    regions: list[str]
    source_slugs: list[str]  # every source carrying the story (for the source filter)
    # Every outlet that reported it: the real publisher for aggregated items (Google News:
    # "Reuters"), else the source's name. Drives "Sources in this feed" and its filter.
    outlets: list[str]
    source_count: int
    article_count: int
    first_published_at: datetime
    last_published_at: datetime
    score: int | None  # None in the saved list when the story isn't scored for any profile
    tier: str | None
    pinned: bool
    alert: bool  # at or above the profile's alert threshold
    reasons: list[dict]
    is_read: bool
    is_saved: bool
    articles: list[FeedArticle]
    ai: AiSummary | None = None
    ai_locked: bool = False  # a summary exists, but the user's plan doesn't include AI


class FeedProfile(BaseModel):
    id: uuid.UUID
    name: str
    show_threshold: int
    alert_threshold: int


class FeedOut(BaseModel):
    profile: FeedProfile
    total: int
    unread: int
    next_offset: int | None
    items: list[FeedItem]
    delay_minutes: int = 0  # the free plan sees stories this long after they're published


class SavedOut(BaseModel):
    total: int
    next_offset: int | None
    items: list[FeedItem]


class MarkReadIn(BaseModel):
    """Either explicit story ids, or "everything currently in this feed view"."""

    story_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    profile_id: uuid.UUID | None = None
    since_hours: int = Field(default=24, ge=1, le=168)


async def _resolve_profile(
    db: AsyncSession, user_id: uuid.UUID, profile_id: uuid.UUID | None
) -> Profile:
    if profile_id is not None:
        return await profiles.get_owned_profile(db, user_id, profile_id)
    profile = await profiles.default_profile(db, user_id)
    if profile is None:
        raise ProfileError(404, "no_profile", "Create a profile to see your feed.")
    return profile


async def build_items(
    db: AsyncSession,
    rows: list[tuple[StoryScore | None, Story, StoryUserState | None]],
    alert_threshold: int | None,
    *,
    ai_allowed: bool = True,
) -> list[FeedItem]:
    story_ids = [story.id for _, story, _ in rows]
    if not story_ids:
        return []
    names = await profiles.source_slugs(db)
    source_by_id = {
        sid: name
        for sid, name in await db.execute(
            select(Source.id, Source.name).where(
                Source.id.in_({story.lead_source_id for _, story, _ in rows})
            )
        )
    }
    articles: dict[uuid.UUID, list[FeedArticle]] = defaultdict(list)
    outlets: dict[uuid.UUID, set[str]] = defaultdict(set)
    for a, slug in await db.execute(
        select(Article, Source.slug)
        .join(Source, Source.id == Article.source_id)
        .where(Article.story_id.in_(story_ids))
        .order_by(Article.published_at)
    ):
        outlets[a.story_id].add(a.publisher or names.get(slug, slug))
        if len(articles[a.story_id]) < MAX_ARTICLES_PER_ITEM:
            articles[a.story_id].append(
                FeedArticle(
                    source=a.publisher or names.get(slug, slug), title=a.title, url=a.url,
                    published_at=a.published_at,
                )
            )  # fmt: skip

    summaries = await summaries_for(db, story_ids) if get_settings().feature_ai else {}

    def ai(story: Story) -> AiSummary | None:
        row = summaries.get(story.id)
        if row is None or not row.summary or not ai_allowed:
            return None
        return AiSummary(
            summary=row.summary,
            key_facts=row.key_facts,
            affected=row.affected,
            outdated=row.article_count < story.article_count,
            updated_at=row.updated_at,
        )

    return [
        FeedItem(
            story_id=story.id,
            title=story.title,
            url=story.url,
            lead_source=story.lead_publisher or source_by_id.get(story.lead_source_id, ""),
            event_type=story.event_type,
            is_routine=story.is_routine,
            topics=story.topics,
            entities=story.entities,
            regions=story.regions,
            source_slugs=story.source_slugs,
            outlets=sorted(outlets.get(story.id, ())),
            source_count=story.source_count,
            article_count=story.article_count,
            first_published_at=story.first_published_at,
            last_published_at=story.last_published_at,
            score=score.score if score else None,
            tier=tier_of(score.score) if score else None,
            pinned=score.pinned if score else False,
            alert=bool(
                score
                and alert_threshold is not None
                and (score.rule_score if score.rule_score is not None else score.score)
                >= alert_threshold
            ),
            reasons=score.reasons if score else [],
            is_read=bool(state and state.read_at),
            is_saved=bool(state and state.saved_at),
            articles=articles.get(story.id, []),
            ai=ai(story),
            ai_locked=not ai_allowed and story.id in summaries,
        )
        for score, story, state in rows
    ]


def _state_join(user_id: uuid.UUID):
    return and_(StoryUserState.story_id == Story.id, StoryUserState.user_id == user_id)


@router.get("/feed", response_model=FeedOut)
async def feed(
    current: CurrentUserDep,
    db: SessionDep,
    profile_id: uuid.UUID | None = None,
    sort: Literal["top", "latest"] = "top",
    since_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    min_score: Annotated[int | None, Query(ge=20, le=100)] = None,
    tier: Tier | None = None,
    topic: str | None = None,
    entity: str | None = None,
    event_type: str | None = None,
    source: Annotated[str | None, Query(max_length=64)] = None,
    outlet: Annotated[str | None, Query(max_length=200)] = None,
    include_routine: bool = True,
    unread_only: bool = False,
    q: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> FeedOut:
    """Your ranked stories. Scores are precomputed, so this is a plain indexed read.

    sort=top orders by score then recency; sort=latest by time. By default only stories at
    or above the profile's show threshold are returned. `q` searches headlines and excerpts.
    """
    profile = await _resolve_profile(db, current.user_id, profile_id)
    plan = await entitlements(db, current.user_id)
    floor = min_score if min_score is not None else profile.show_threshold
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=since_hours)
    conditions = [
        StoryScore.profile_id == profile.id,
        StoryScore.story_published_at >= cutoff,
        StoryScore.score >= floor,
    ]
    if plan.feed_delay_minutes:
        conditions.append(
            StoryScore.story_published_at <= now - timedelta(minutes=plan.feed_delay_minutes)
        )
    if tier:
        conditions += [
            StoryScore.score >= TIER_FLOORS[tier],
            StoryScore.score < TIER_CEILINGS[tier],
        ]
    if topic:
        conditions.append(Story.topics.contains([topic]))
    if entity:
        conditions.append(Story.entities.contains([entity]))
    if source:
        conditions.append(Story.source_slugs.contains([source]))
    if outlet:
        # Stories with at least one report from this outlet (publisher or source name).
        by_outlet = (
            select(Article.story_id)
            .join(Source, Source.id == Article.source_id)
            .where(func.coalesce(Article.publisher, Source.name) == outlet)
        )
        conditions.append(Story.id.in_(by_outlet))
    if event_type:
        conditions.append(Story.event_type == event_type)
    if not include_routine:
        conditions.append(Story.is_routine.is_(False))
    if q:
        conditions.append(Story.match_text.contains(q.strip().lower(), autoescape=True))

    base = (
        select(StoryScore, Story, StoryUserState)
        .join(Story, Story.id == StoryScore.story_id)
        .outerjoin(StoryUserState, _state_join(current.user_id))
        .where(*conditions)
    )
    counts = base.with_only_columns(
        func.count(),
        func.count().filter(StoryUserState.read_at.is_(None)),
    ).order_by(None)
    total, unread = (await db.execute(counts)).one()
    if unread_only:
        base = base.where(StoryUserState.read_at.is_(None))
        total = unread

    order = (
        (StoryScore.score.desc(), StoryScore.story_published_at.desc())
        if sort == "top"
        else (StoryScore.story_published_at.desc(), StoryScore.score.desc())
    )
    rows = (await db.execute(base.order_by(*order, Story.id).limit(limit).offset(offset))).all()

    return FeedOut(
        profile=FeedProfile(
            id=profile.id,
            name=profile.name,
            show_threshold=profile.show_threshold,
            alert_threshold=profile.alert_threshold,
        ),
        total=total,
        unread=unread,
        next_offset=offset + limit if offset + limit < total else None,
        items=await build_items(
            db, [tuple(r) for r in rows], profile.alert_threshold, ai_allowed=plan.ai_summaries
        ),
        delay_minutes=plan.feed_delay_minutes,
    )


@router.post("/feed/mark-read", status_code=status.HTTP_200_OK)
async def mark_read(body: MarkReadIn, current: CurrentUserDep, db: SessionDep) -> dict:
    """Mark stories read: the given ids, or every story shown in a profile's feed window."""
    now = datetime.now(UTC)
    if body.story_ids is not None:
        story_ids = list(body.story_ids)
    else:
        profile = await _resolve_profile(db, current.user_id, body.profile_id)
        cutoff = now - timedelta(hours=body.since_hours)
        story_ids = list(
            await db.scalars(
                select(StoryScore.story_id).where(
                    StoryScore.profile_id == profile.id,
                    StoryScore.story_published_at >= cutoff,
                    StoryScore.score >= profile.show_threshold,
                )
            )
        )
    if story_ids:
        stmt = pg_insert(StoryUserState).values(
            [{"user_id": current.user_id, "story_id": sid, "read_at": now} for sid in story_ids]
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[StoryUserState.user_id, StoryUserState.story_id],
                set_={"read_at": func.coalesce(StoryUserState.read_at, stmt.excluded.read_at)},
            )
        )
        await db.commit()
    return {"marked": len(story_ids)}


async def _set_state(
    db: AsyncSession, user_id: uuid.UUID, story_id: uuid.UUID, **values: datetime | None
) -> None:
    if await db.get(Story, story_id) is None:
        raise ProfileError(404, "story_not_found", "Story not found.")
    stmt = pg_insert(StoryUserState).values(user_id=user_id, story_id=story_id, **values)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[StoryUserState.user_id, StoryUserState.story_id], set_=values
        )
    )
    await db.commit()


@router.put("/stories/{story_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def read(story_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    await _set_state(db, current.user_id, story_id, read_at=datetime.now(UTC))


@router.delete("/stories/{story_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def unread(story_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    await _set_state(db, current.user_id, story_id, read_at=None)


@router.put("/stories/{story_id}/saved", status_code=status.HTTP_204_NO_CONTENT)
async def save(story_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    cap = (await entitlements(db, current.user_id)).max_saved
    if cap is not None:
        saved = await db.scalar(
            select(func.count()).where(
                StoryUserState.user_id == current.user_id,
                StoryUserState.saved_at.is_not(None),
                StoryUserState.story_id != story_id,
            )
        )
        if saved >= cap:
            raise PlanLimitError(
                "saved_limit", f"The free plan keeps up to {cap} saved stories. Upgrade for more."
            )
    await _set_state(db, current.user_id, story_id, saved_at=datetime.now(UTC))


@router.delete("/stories/{story_id}/saved", status_code=status.HTTP_204_NO_CONTENT)
async def unsave(story_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    await _set_state(db, current.user_id, story_id, saved_at=None)


@router.get("/saved", response_model=SavedOut)
async def saved(
    current: CurrentUserDep,
    db: SessionDep,
    profile_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=5000)] = 0,
) -> SavedOut:
    """Saved stories, newest saved first, kept with no time limit. Scores (and reasons) are
    shown from the given or default profile where that profile still has them."""
    profile = await _resolve_profile(db, current.user_id, profile_id)
    base = (
        select(StoryScore, Story, StoryUserState)
        .select_from(StoryUserState)
        .join(Story, Story.id == StoryUserState.story_id)
        .outerjoin(
            StoryScore, and_(StoryScore.story_id == Story.id, StoryScore.profile_id == profile.id)
        )
        .where(StoryUserState.user_id == current.user_id, StoryUserState.saved_at.is_not(None))
    )
    if q:
        base = base.where(Story.match_text.contains(q.strip().lower(), autoescape=True))
    total = await db.scalar(base.with_only_columns(func.count()).order_by(None))
    rows = (
        await db.execute(
            base.order_by(StoryUserState.saved_at.desc(), Story.id).limit(limit).offset(offset)
        )
    ).all()
    return SavedOut(
        total=total,
        next_offset=offset + limit if offset + limit < total else None,
        items=await build_items(
            db,
            [tuple(r) for r in rows],
            profile.alert_threshold,
            ai_allowed=(await entitlements(db, current.user_id)).ai_summaries,
        ),
    )


# --------------------------------------------------------------------------- refresh now


class RefreshOut(BaseModel):
    running: bool
    total: int  # sources being checked in this run
    done: int
    started_at: str | None
    next_allowed_in: int  # seconds until a click starts a new run (until then it joins this one)


@router.post("/feed/refresh", response_model=RefreshOut)
async def start_refresh(current: CurrentUserDep, db: SessionDep) -> dict:
    """Check every healthy source now. Runs are shared and spaced out, so this is safe
    to click repeatedly; the per-user limit only stops scripts."""
    if not await within_rate(redis_client, f"refresh:{current.user_id}", 20, 600):
        raise HTTPException(
            429,
            detail={"code": "slow_down", "message": "Too many refreshes. Try again later."},
        )
    return await refresh.start(db, redis_client)


@router.get("/feed/refresh", response_model=RefreshOut)
async def refresh_status(current: CurrentUserDep, db: SessionDep) -> dict:
    return await refresh.status(db, redis_client)
