"""Integration tests for processing: need the Docker PostgreSQL (see conftest)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.ingestion.normalize import canonical_url, normalise_title_for_hash, sha256
from app.models import Article, Source, Story
from app.processing.service import (
    PROCESSING_LOCK_KEY,
    process_all_pending,
    process_pending,
    refresh_stories,
    reset_processing,
)
from app.processing.taxonomy import TAXONOMY_VERSION

pytestmark = pytest.mark.usefixtures("db")

T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


async def make_source(db, slug: str, homepage: str, **kw) -> uuid.UUID:
    values = {
        "slug": slug,
        "name": slug,
        "kind": "rss",
        "url": f"{homepage}/rss/{slug}.xml",
        "homepage": homepage,
        "category": "press_india",
        "region": "IN",
        "licence_class": "headline_link",
        "authority": 3,
        "poll_interval_seconds": 60,
    } | kw
    async with db() as session, session.begin():
        source = Source(**values)
        session.add(source)
        await session.flush()
        return source.id


async def add_article(db, source_id, title: str, *, at: datetime = T0, excerpt=None) -> uuid.UUID:
    url = canonical_url(f"https://example.com/{uuid.uuid4().hex}")
    async with db() as session, session.begin():
        article = Article(
            source_id=source_id,
            guid=url,
            url=url,
            url_hash=sha256(url),
            title=title,
            title_hash=sha256(normalise_title_for_hash(title)),
            excerpt=excerpt,
            published_at=at,
        )
        session.add(article)
        await session.flush()
        return article.id


@pytest.fixture
async def sources(db) -> dict[str, uuid.UUID]:
    return {
        "mint": await make_source(db, "mint-markets", "https://www.livemint.com"),
        "et": await make_source(db, "et-markets", "https://economictimes.indiatimes.com"),
        "rbi": await make_source(
            db,
            "rbi-press-releases",
            "https://www.rbi.org.in",
            category="official_india",
            licence_class="official",
            authority=5,
        ),
        "rbi2": await make_source(
            db,
            "rbi-notifications",
            "https://www.rbi.org.in",
            category="official_india",
            licence_class="official",
            authority=5,
        ),
    }


async def stories(db) -> list[Story]:
    async with db() as session:
        return list((await session.scalars(select(Story).order_by(Story.first_published_at))).all())


async def test_articles_are_tagged_and_grouped_into_stories(db, sources) -> None:
    await add_article(
        db, sources["mint"], "Airtel Money IPO: London's biggest listing in 5 years makes debut"
    )
    await add_article(
        db,
        sources["et"],
        "Airtel Money debuts on LSE in biggest listing in 5 years",
        at=T0 + timedelta(minutes=20),
    )
    await add_article(
        db, sources["et"], "Gold hits one-week high on bargain-hunting", at=T0 + timedelta(hours=1)
    )

    result = await process_pending(db)

    assert (result.articles, result.new_stories) == (3, 2)
    airtel, gold = await stories(db)
    assert (airtel.article_count, airtel.source_count) == (2, 2)
    assert airtel.event_type == "ipo_listing"
    assert "bharti_airtel" in airtel.entities
    assert airtel.first_published_at == T0
    assert airtel.last_published_at == T0 + timedelta(minutes=20)
    assert (gold.article_count, gold.entities) == (1, ["gold"])

    async with db() as session:
        pending = await session.scalar(
            select(func.count()).select_from(Article).where(Article.processed_at.is_(None))
        )
        versions = set((await session.scalars(select(Article.tag_version))).all())
    assert pending == 0
    assert versions == {TAXONOMY_VERSION}


async def test_official_source_becomes_the_lead_when_it_joins(db, sources) -> None:
    await add_article(
        db, sources["et"], "RBI to conduct Rs 25,000 cr OMO sale auction of government securities"
    )
    await process_pending(db)
    await add_article(
        db,
        sources["rbi"],
        "RBI announces OMO Sale of Government of India Securities",
        at=T0 + timedelta(minutes=30),
    )
    await process_pending(db)

    [story] = await stories(db)
    assert story.title == "RBI announces OMO Sale of Government of India Securities"
    assert story.max_authority == 5
    assert story.has_official_source
    assert story.is_routine
    assert story.source_count == 2


async def test_same_publisher_feeds_do_not_merge_similar_titles(db, sources) -> None:
    await add_article(db, sources["rbi"], "Reserve Bank of India – Bulletin Weekly Statistical")
    await add_article(
        db,
        sources["rbi2"],
        "Reserve Bank of India (Commercial Banks – Prudential Norms) Amendment",
        at=T0 + timedelta(minutes=5),
    )
    await process_pending(db)
    assert len(await stories(db)) == 2


async def test_processing_is_incremental_and_idempotent(db, sources) -> None:
    await add_article(db, sources["mint"], "GST Council meet: Tax officers' arrest power scrapped")
    first = await process_pending(db)
    again = await process_pending(db)

    assert first.articles == 1
    assert again.articles == 0 and not again.skipped
    assert len(await stories(db)) == 1


async def test_batches_drain_backlog(db, sources) -> None:
    for i in range(5):
        await add_article(
            db, sources["mint"], f"Unrelated headline number {i} about topic {i * 7}",
            at=T0 + timedelta(minutes=i),
        )  # fmt: skip
    totals = await process_all_pending(db)
    assert totals.articles == 5
    assert len(totals.touched_story_ids) == 5


async def test_second_worker_skips_while_lane_is_busy(db, sources) -> None:
    await add_article(db, sources["mint"], "Gold hits one-week high")
    holder_ready = asyncio.Event()
    release = asyncio.Event()

    async def hold_lock() -> None:
        async with db() as session, session.begin():
            await session.execute(select(func.pg_advisory_xact_lock(PROCESSING_LOCK_KEY)))
            holder_ready.set()
            await release.wait()

    holder = asyncio.create_task(hold_lock())
    await holder_ready.wait()
    result = await process_pending(db)
    release.set()
    await holder

    assert result.skipped
    assert (await process_pending(db)).articles == 1


async def test_refresh_removes_stories_left_without_articles(db, sources) -> None:
    keep = await add_article(db, sources["mint"], "Gold hits one-week high")
    drop = await add_article(db, sources["et"], "Hindalco Q2 results: net profit jumps 20%")
    await process_pending(db)
    story_ids = {s.id for s in await stories(db)}

    async with db() as session, session.begin():
        await session.execute(delete(Article).where(Article.id == drop))
        await refresh_stories(session, story_ids)

    [remaining] = await stories(db)
    async with db() as session:
        assert (await session.get(Article, keep)).story_id == remaining.id


async def test_reprocess_rebuilds_stories(db, sources) -> None:
    await add_article(db, sources["mint"], "Gold hits one-week high")
    await add_article(db, sources["et"], "Gold hits one-week high", at=T0 + timedelta(minutes=3))
    await process_pending(db)
    [before] = await stories(db)

    assert await reset_processing(db) == 2
    assert await stories(db) == []
    await process_pending(db)

    [after] = await stories(db)
    assert after.id != before.id
    assert after.article_count == 2
