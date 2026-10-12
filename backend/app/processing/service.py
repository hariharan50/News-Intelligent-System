"""Turn newly ingested articles into tagged stories.

process_pending() runs as a single lane (a PostgreSQL advisory lock): clustering compares
each article with those already placed, so two concurrent batches could each open a new
story for the same event. One lane processing a few hundred articles per second is far
above our ingest rate.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Article, LicenceClass, Source, Story
from app.processing.clustering import (
    WINDOW,
    ArticleFeatures,
    ClusterIndex,
    significant_words,
)
from app.processing.tagger import Tagger, get_tagger
from app.processing.taxonomy import EVENT_TYPES_BY_SLUG, TAXONOMY_VERSION

log = logging.getLogger(__name__)

# Arbitrary constant identifying the processing lane's advisory lock ("NISP").
PROCESSING_LOCK_KEY = 0x4E495350
DEFAULT_BATCH_SIZE = 200
MATCH_TEXT_MAX_CHARS = 4000


@dataclass(frozen=True, slots=True)
class ProcessResult:
    articles: int = 0
    new_stories: int = 0
    updated_stories: int = 0
    touched_story_ids: tuple[uuid.UUID, ...] = ()
    skipped: bool = False  # another worker holds the processing lane


def publisher_of(homepage: str | None, feed_url: str) -> str:
    """Feeds of one publisher share a homepage host: all RBI feeds -> rbi.org.in."""
    host = (urlsplit(homepage or feed_url).hostname or "").lower()
    return host.removeprefix("www.")


async def _load_index(session: AsyncSession, since: datetime) -> ClusterIndex:
    rows = await session.execute(
        select(
            Article.id,
            Article.story_id,
            Article.title,
            Article.title_hash,
            Article.entities,
            Article.event_type,
            Article.published_at,
            Article.publisher,
            Source.homepage,
            Source.url,
        )
        .join(Source, Source.id == Article.source_id)
        .where(Article.story_id.is_not(None), Article.published_at >= since)
    )
    index = ClusterIndex()
    for r in rows:
        index.add(
            ArticleFeatures(
                article_id=r.id,
                publisher=article_publisher(r.publisher, r.homepage, r.url),
                title_hash=r.title_hash,
                words=significant_words(r.title),
                entities=frozenset(r.entities),
                event_type=r.event_type,
                published_at=r.published_at,
                story_id=r.story_id,
            )
        )
    return index


def article_publisher(publisher: str | None, homepage: str | None, feed_url: str) -> str:
    """Who published an article, for the "never merge one publisher's own reports" rule.
    Aggregated items (Google News) carry their real publisher; others use the source host."""
    if publisher:
        return f"named:{publisher.strip().lower()}"
    return publisher_of(homepage, feed_url)


async def refresh_stories(session: AsyncSession, story_ids: set[uuid.UUID]) -> None:
    """Recompute every derived column of the given stories from their articles."""
    if not story_ids:
        return
    rows = await session.execute(
        select(
            Article.story_id,
            Article.title,
            Article.url,
            Article.source_id,
            Article.published_at,
            Article.topics,
            Article.entities,
            Article.regions,
            Article.event_type,
            Article.excerpt,
            Article.publisher,
            Source.slug,
            Source.authority,
            Source.licence_class,
        )
        .join(Source, Source.id == Article.source_id)
        .where(Article.story_id.in_(story_ids))
    )
    by_story: dict[uuid.UUID, list] = defaultdict(list)
    for r in rows:
        by_story[r.story_id].append(r)

    # A story whose articles were all deleted (retention, source removal) goes too.
    if empty := story_ids - by_story.keys():
        await session.execute(delete(Story).where(Story.id.in_(empty)))

    now = datetime.now(UTC)
    updates = []
    for story_id, articles in by_story.items():
        # Lead article: the most authoritative source, earliest first. When RBI's own
        # release joins a story the press broke, RBI's headline and link take over.
        ranked = sorted(articles, key=lambda a: (-a.authority, a.published_at))
        lead = ranked[0]
        event_type = next((a.event_type for a in ranked if a.event_type), None)
        updates.append(
            {
                "id": story_id,
                "title": lead.title,
                "url": lead.url,
                "lead_source_id": lead.source_id,
                "lead_publisher": lead.publisher,
                "event_type": event_type,
                "is_routine": bool(event_type and EVENT_TYPES_BY_SLUG[event_type].routine),
                "topics": sorted({t for a in articles for t in a.topics}),
                "entities": sorted({e for a in articles for e in a.entities}),
                "regions": sorted({g for a in articles for g in a.regions}),
                "match_text": "\n".join(
                    filter(None, (part for a in ranked for part in (a.title, a.excerpt)))
                ).lower()[:MATCH_TEXT_MAX_CHARS],
                "source_slugs": sorted({a.slug for a in articles}),
                "article_count": len(articles),
                # Outlets, not feeds: Google News items from different publishers count apart.
                "source_count": len({(a.source_id, a.publisher) for a in articles}),
                "max_authority": lead.authority,
                "has_official_source": any(
                    a.licence_class == LicenceClass.OFFICIAL for a in articles
                ),
                "first_published_at": min(a.published_at for a in articles),
                "last_published_at": max(a.published_at for a in articles),
                "updated_at": now,
            }
        )
    if updates:
        await session.execute(update(Story), updates)


async def process_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    tagger: Tagger | None = None,
) -> ProcessResult:
    """Tag and cluster up to `batch_size` unprocessed articles, oldest first."""
    tagger = tagger or get_tagger()
    async with session_factory() as session, session.begin():
        if not await session.scalar(select(func.pg_try_advisory_xact_lock(PROCESSING_LOCK_KEY))):
            return ProcessResult(skipped=True)

        rows = (
            await session.execute(
                select(Article, Source.slug, Source.region, Source.homepage, Source.url)
                .join(Source, Source.id == Article.source_id)
                .where(Article.processed_at.is_(None))
                .order_by(Article.published_at, Article.fetched_at)
                .limit(batch_size)
            )
        ).all()
        if not rows:
            return ProcessResult()

        index = await _load_index(session, since=rows[0].Article.published_at - WINDOW)
        new_stories: dict[uuid.UUID, Story] = {}
        assignments: list[tuple[Article, uuid.UUID]] = []

        for article, slug, region, homepage, feed_url in rows:
            tags = tagger.tag(
                title=article.title,
                excerpt=article.excerpt,
                feed_tags=article.feed_tags,
                source_slug=slug,
                source_region=region,
            )
            article.topics = list(tags.topics)
            article.entities = list(tags.entities)
            article.regions = list(tags.regions)
            article.event_type = tags.event_type
            article.tag_version = TAXONOMY_VERSION

            features = ArticleFeatures(
                article_id=article.id,
                publisher=article_publisher(article.publisher, homepage, feed_url),
                title_hash=article.title_hash,
                words=significant_words(article.title),
                entities=frozenset(tags.entities),
                event_type=tags.event_type,
                published_at=article.published_at,
            )
            match = index.find(features)
            if match is not None:
                story_id = match.story_id
            else:
                story_id = uuid.uuid4()
                new_stories[story_id] = Story(
                    id=story_id,
                    title=article.title,
                    url=article.url,
                    lead_source_id=article.source_id,
                    lead_publisher=article.publisher,
                    max_authority=1,
                    first_published_at=article.published_at,
                    last_published_at=article.published_at,
                )
            assignments.append((article, story_id))
            index.add(replace(features, story_id=story_id))

        # Stories must exist before articles point at them (no ORM relationship orders this).
        session.add_all(new_stories.values())
        await session.flush()
        now = datetime.now(UTC)
        for article, story_id in assignments:
            article.story_id = story_id
            article.processed_at = now
        await session.flush()

        touched = {story_id for _, story_id in assignments}
        await refresh_stories(session, touched)

    result = ProcessResult(
        articles=len(rows),
        new_stories=len(new_stories),
        updated_stories=len(touched - new_stories.keys()),
        touched_story_ids=tuple(touched),
    )
    log.info(
        "processed %d article(s): %d new story(ies), %d updated",
        result.articles,
        result.new_stories,
        result.updated_stories,
    )
    return result


async def process_all_pending(
    session_factory: async_sessionmaker[AsyncSession], *, max_batches: int = 50
) -> ProcessResult:
    """Drain the backlog in batches. Returns totals."""
    totals = ProcessResult()
    touched: set[uuid.UUID] = set()
    for _ in range(max_batches):
        result = await process_pending(session_factory)
        if result.skipped or result.articles == 0:
            break
        touched.update(result.touched_story_ids)
        totals = ProcessResult(
            articles=totals.articles + result.articles,
            new_stories=totals.new_stories + result.new_stories,
            updated_stories=totals.updated_stories + result.updated_stories,
        )
    return replace(totals, touched_story_ids=tuple(touched))


async def reset_processing(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Forget all stories and tags so everything is re-processed (after taxonomy changes)."""
    async with session_factory() as session, session.begin():
        await session.execute(select(func.pg_advisory_xact_lock(PROCESSING_LOCK_KEY)))
        result = await session.execute(
            update(Article).values(
                story_id=None,
                processed_at=None,
                topics=[],
                entities=[],
                regions=[],
                event_type=None,
                tag_version=None,
            )
        )
        await session.execute(delete(Story))
        return result.rowcount
