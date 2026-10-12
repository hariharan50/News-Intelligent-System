"""Read-only views of ingestion and processing for development.

Mounted only when ENVIRONMENT is local or test: there is no authentication until Phase 3.
They are replaced by the admin API (sources, health) and the feed API in later phases.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.session import SessionDep
from app.models import Article, Source, Story
from app.processing.taxonomy import ENTITIES, EVENT_TYPES, TAXONOMY_VERSION, TOPICS

router = APIRouter(prefix="/dev", tags=["dev"])


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    category: str
    licence_class: str
    enabled: bool
    is_priority: bool
    poll_interval_seconds: int
    status: str
    consecutive_failures: int
    last_polled_at: datetime | None
    last_success_at: datetime | None
    last_new_item_at: datetime | None
    next_poll_at: datetime
    last_error: str | None
    notes: str | None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    title: str
    url: str
    excerpt: str | None
    published_at: datetime
    published_at_estimated: bool
    fetched_at: datetime


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(session: SessionDep) -> list[Source]:
    result = await session.execute(
        select(Source).order_by(Source.enabled.desc(), Source.category, Source.slug)
    )
    return list(result.scalars())


@router.get("/articles", response_model=list[ArticleOut])
async def latest_articles(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    source: Annotated[str | None, Query(description="Filter by source slug")] = None,
) -> list[ArticleOut]:
    stmt = (
        select(Article, Source.slug)
        .join(Source, Source.id == Article.source_id)
        .order_by(Article.published_at.desc())
        .limit(limit)
    )
    if source:
        stmt = stmt.where(Source.slug == source)
    rows = (await session.execute(stmt)).all()
    return [
        ArticleOut(
            id=a.id,
            source=slug,
            title=a.title,
            url=a.url,
            excerpt=a.excerpt,
            published_at=a.published_at,
            published_at_estimated=a.published_at_estimated,
            fetched_at=a.fetched_at,
        )
        for a, slug in rows
    ]


class StoryArticleOut(BaseModel):
    source: str
    source_name: str
    title: str
    url: str
    published_at: datetime


class StoryOut(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    lead_source: str
    event_type: str | None
    is_routine: bool
    topics: list[str]
    entities: list[str]
    regions: list[str]
    article_count: int
    source_count: int
    max_authority: int
    has_official_source: bool
    first_published_at: datetime
    last_published_at: datetime
    articles: list[StoryArticleOut]


@router.get("/stories", response_model=list[StoryOut])
async def latest_stories(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    topic: Annotated[str | None, Query()] = None,
    entity: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    include_routine: Annotated[bool, Query()] = True,
    multi_source_only: Annotated[bool, Query()] = False,
) -> list[StoryOut]:
    stmt = (
        select(Story, Source.slug)
        .join(Source, Source.id == Story.lead_source_id)
        .order_by(Story.last_published_at.desc())
        .limit(limit)
    )
    if topic:
        stmt = stmt.where(Story.topics.contains([topic]))
    if entity:
        stmt = stmt.where(Story.entities.contains([entity]))
    if event_type:
        stmt = stmt.where(Story.event_type == event_type)
    if not include_routine:
        stmt = stmt.where(Story.is_routine.is_(False))
    if multi_source_only:
        stmt = stmt.where(Story.source_count > 1)
    stories = (await session.execute(stmt)).all()

    articles_by_story: dict[uuid.UUID, list[StoryArticleOut]] = {s.id: [] for s, _ in stories}
    if stories:
        rows = await session.execute(
            select(Article, Source.slug, Source.name)
            .join(Source, Source.id == Article.source_id)
            .where(Article.story_id.in_(articles_by_story))
            .order_by(Article.published_at)
        )
        for a, slug, name in rows:
            articles_by_story[a.story_id].append(
                StoryArticleOut(
                    source=slug,
                    source_name=name,
                    title=a.title,
                    url=a.url,
                    published_at=a.published_at,
                )
            )

    return [
        StoryOut(
            id=s.id,
            title=s.title,
            url=s.url,
            lead_source=lead_slug,
            event_type=s.event_type,
            is_routine=s.is_routine,
            topics=s.topics,
            entities=s.entities,
            regions=s.regions,
            article_count=s.article_count,
            source_count=s.source_count,
            max_authority=s.max_authority,
            has_official_source=s.has_official_source,
            first_published_at=s.first_published_at,
            last_published_at=s.last_published_at,
            articles=articles_by_story[s.id],
        )
        for s, lead_slug in stories
    ]


class TaxonomyOut(BaseModel):
    version: int
    topics: dict[str, str]
    entities: dict[str, str]
    event_types: dict[str, str]


@router.get("/taxonomy", response_model=TaxonomyOut)
async def taxonomy() -> TaxonomyOut:
    """Display names for tag slugs."""
    return TaxonomyOut(
        version=TAXONOMY_VERSION,
        topics={t.slug: t.name for t in TOPICS},
        entities={e.slug: e.name for e in ENTITIES},
        event_types={e.slug: e.name for e in EVENT_TYPES},
    )


@router.get("/emails")
async def dev_emails() -> list[dict]:
    """Emails the console mailer "sent" (EMAIL_PROVIDER=console), newest first."""
    import json

    from app.cache.redis import redis_client
    from app.email.sender import DEV_OUTBOX_KEY

    return [json.loads(e) for e in await redis_client.lrange(DEV_OUTBOX_KEY, 0, 49)]
