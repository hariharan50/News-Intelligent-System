"""Integration tests for polling: need the Docker PostgreSQL and Redis (see conftest)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from app.cache.redis import redis_client
from app.ingestion.adapters import FeedItem, FetchError, FetchResult, SourceRef
from app.ingestion.seed import LAUNCH_SOURCES, seed_sources
from app.ingestion.service import claim_due_sources, next_poll_delay, poll_source
from app.models import Article, FetchOutcome, Source, SourceFetch, SourceStatus

pytestmark = pytest.mark.usefixtures("db")

T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def item(n: int, **kw) -> FeedItem:
    defaults = {
        "guid": f"guid-{n}",
        "url": f"https://news.example/story/{n}?utm_source=rss",
        "title": f"Story number {n}",
        "excerpt": f"Excerpt {n}",
        "published_at": T0 - timedelta(minutes=n),
    }
    return FeedItem(**(defaults | kw))


class FakeAdapter:
    """Returns queued results (or raises queued errors) in order."""

    def __init__(self, *results: FetchResult | Exception) -> None:
        self.results = list(results)
        self.calls: list[SourceRef] = []

    async def fetch(self, client: httpx.AsyncClient, source: SourceRef) -> FetchResult:
        self.calls.append(source)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def ok(*items: FeedItem, etag: str | None = None) -> FetchResult:
    return FetchResult(http_status=200, items=list(items), etag=etag)


async def make_source(session_factory, **kw) -> uuid.UUID:
    values = {
        "slug": f"test-{uuid.uuid4().hex[:8]}",
        "name": "Test source",
        "kind": "rss",
        "url": f"https://{uuid.uuid4().hex[:8]}.example/rss.xml",
        "category": "press_india",
        "region": "IN",
        "licence_class": "headline_link",
        "authority": 3,
        "poll_interval_seconds": 60,
    } | kw
    async with session_factory() as session, session.begin():
        source = Source(**values)
        session.add(source)
        await session.flush()
        return source.id


async def run_poll(session_factory, source_id, adapter) -> object:
    async with httpx.AsyncClient() as client:
        return await poll_source(
            source_id,
            session_factory=session_factory,
            client=client,
            redis=redis_client,
            adapter_for=lambda _kind: adapter,
        )


async def count(session_factory, model, *where) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model).where(*where))


async def get_source(session_factory, source_id) -> Source:
    async with session_factory() as session:
        return await session.get(Source, source_id)


# --------------------------------------------------------------------------- polling


async def test_poll_stores_articles_and_repoll_adds_no_duplicates(db) -> None:
    source_id = await make_source(db)
    adapter = FakeAdapter(ok(item(1), item(2), etag='"a"'), ok(item(1), item(2), item(3)))

    first = await run_poll(db, source_id, adapter)
    second = await run_poll(db, source_id, adapter)

    assert (first.outcome, first.items_seen, first.items_new) == (FetchOutcome.OK, 2, 2)
    assert (second.items_seen, second.items_new) == (3, 1)
    assert await count(db, Article) == 3
    assert await count(db, SourceFetch) == 2

    source = await get_source(db, source_id)
    assert source.status == SourceStatus.HEALTHY
    assert source.consecutive_failures == 0
    assert source.last_new_item_at is not None
    # The second poll got no ETag, so the stored validator is cleared rather than kept stale.
    assert adapter.calls[1].etag == '"a"'
    assert source.etag is None

    async with db() as session:
        article = await session.scalar(select(Article).where(Article.guid == "guid-1"))
    assert article.url == "https://news.example/story/1"  # tracking parameters removed
    assert article.excerpt == "Excerpt 1"


async def test_same_url_from_two_feeds_is_stored_once(db) -> None:
    a = await make_source(db)
    b = await make_source(db)

    await run_poll(db, a, FakeAdapter(ok(item(1, guid="feed-a-1"))))
    result = await run_poll(db, b, FakeAdapter(ok(item(1, guid="feed-b-1"))))

    assert result.items_new == 0
    assert await count(db, Article) == 1


async def test_duplicates_within_one_response_are_collapsed(db) -> None:
    source_id = await make_source(db)
    result = await run_poll(
        db, source_id, FakeAdapter(ok(item(1), item(1), item(2, guid="guid-1")))
    )
    assert (result.items_seen, result.items_new) == (3, 1)


async def test_not_modified_keeps_validators_and_counts_as_healthy(db) -> None:
    source_id = await make_source(db)
    adapter = FakeAdapter(
        ok(item(1), etag='"v1"'),
        FetchResult(http_status=304, items=[], not_modified=True, etag='"v1"'),
    )
    await run_poll(db, source_id, adapter)
    result = await run_poll(db, source_id, adapter)

    assert result.outcome == FetchOutcome.NOT_MODIFIED
    source = await get_source(db, source_id)
    assert source.etag == '"v1"'
    assert source.status == SourceStatus.HEALTHY


async def test_failures_back_off_and_mark_source_degraded_then_failing(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ingest_failing_after", 3)
    source_id = await make_source(db, poll_interval_seconds=30)
    adapter = FakeAdapter(*[FetchError("HTTP 503", http_status=503)] * 3, ok(item(1)))

    before = datetime.now(UTC)
    result = await run_poll(db, source_id, adapter)
    source = await get_source(db, source_id)
    assert result.outcome == FetchOutcome.ERROR
    assert source.status == SourceStatus.DEGRADED
    assert source.consecutive_failures == 1
    assert source.last_error == "HTTP 503"
    # 30s * 2^1 = 60s, +/-20% jitter.
    assert timedelta(seconds=47) < source.next_poll_at - before < timedelta(seconds=73)

    await run_poll(db, source_id, adapter)
    await run_poll(db, source_id, adapter)
    assert (await get_source(db, source_id)).status == SourceStatus.FAILING

    await run_poll(db, source_id, adapter)
    source = await get_source(db, source_id)
    assert (source.status, source.consecutive_failures, source.last_error) == (
        SourceStatus.HEALTHY,
        0,
        None,
    )

    async with db() as session:
        errors = (
            await session.scalars(
                select(SourceFetch.http_status).where(SourceFetch.outcome == FetchOutcome.ERROR)
            )
        ).all()
    assert errors == [503, 503, 503]


async def test_retry_after_is_respected(db) -> None:
    source_id = await make_source(db, poll_interval_seconds=30)
    adapter = FakeAdapter(FetchError("HTTP 429", http_status=429, retry_after=900))

    before = datetime.now(UTC)
    await run_poll(db, source_id, adapter)

    source = await get_source(db, source_id)
    assert source.next_poll_at - before >= timedelta(seconds=899)


async def test_adapter_crash_is_recorded_not_raised(db) -> None:
    source_id = await make_source(db)
    result = await run_poll(db, source_id, FakeAdapter(ValueError("boom")))

    assert result.outcome == FetchOutcome.ERROR
    assert "internal error: ValueError: boom" in result.error
    assert (await get_source(db, source_id)).status == SourceStatus.DEGRADED


async def test_disabled_or_missing_source_is_skipped(db) -> None:
    source_id = await make_source(db, enabled=False)
    adapter = FakeAdapter(ok(item(1)))

    assert (await run_poll(db, source_id, adapter)).outcome is None
    assert (await run_poll(db, uuid.uuid4(), adapter)).outcome is None
    assert adapter.calls == []


async def test_concurrent_polls_of_one_source_run_once(db) -> None:
    source_id = await make_source(db)
    gate = asyncio.Event()

    class SlowAdapter(FakeAdapter):
        async def fetch(self, client, source):
            await gate.wait()
            return await super().fetch(client, source)

    adapter = SlowAdapter(ok(item(1)), ok(item(1)))
    first = asyncio.create_task(run_poll(db, source_id, adapter))
    await asyncio.sleep(0.2)  # let the first poll take the lock
    second = await run_poll(db, source_id, adapter)
    gate.set()
    await first

    assert second.outcome is None
    assert len(adapter.calls) == 1


# --------------------------------------------------------------------------- dispatching


async def test_claim_due_sources_takes_only_due_enabled_sources_once(db) -> None:
    due = await make_source(db)
    disabled = await make_source(db, enabled=False)
    later = await make_source(db)
    async with db() as session, session.begin():
        await session.execute(
            update(Source)
            .where(Source.id == later)
            .values(next_poll_at=func.now() + timedelta(minutes=5))
        )

    claimed = await claim_due_sources(db)
    claimed_again = await claim_due_sources(db)

    assert claimed == [due]
    assert disabled not in claimed
    assert claimed_again == []  # the lease pushed next_poll_at into the future


async def test_successful_poll_schedules_next_poll_at_interval(db) -> None:
    source_id = await make_source(db, poll_interval_seconds=30)
    before = datetime.now(UTC)
    await run_poll(db, source_id, FakeAdapter(ok(item(1))))

    source = await get_source(db, source_id)
    assert timedelta(seconds=29) < source.next_poll_at - before < timedelta(seconds=32)


# --------------------------------------------------------------------------- seeding


async def test_seed_sources_is_idempotent_and_keeps_admin_edits(db) -> None:
    assert await seed_sources(db) == len(LAUNCH_SOURCES)

    async with db() as session, session.begin():
        await session.execute(
            update(Source).where(Source.slug == "bbc-world").values(poll_interval_seconds=900)
        )
    assert await seed_sources(db) == 0

    async with db() as session:
        bbc = await session.scalar(select(Source).where(Source.slug == "bbc-world"))
        priority = (await session.scalars(select(Source.slug).where(Source.is_priority))).all()
    assert bbc.poll_interval_seconds == 900
    assert "rbi-press-releases" in priority


# --------------------------------------------------------------------------- pure


def test_next_poll_delay() -> None:
    mid = lambda: 0.5  # noqa: E731  (no jitter)
    assert next_poll_delay(30, 0, rng=mid) == 30
    assert next_poll_delay(120, 0, rng=mid) == 120
    assert next_poll_delay(30, 1, rng=mid, max_backoff=3600) == 60
    assert next_poll_delay(30, 3, rng=mid, max_backoff=3600) == 240
    assert next_poll_delay(30, 50, rng=mid, max_backoff=3600) == 3600
    assert next_poll_delay(30, 1, rng=mid, retry_after=600, max_backoff=3600) == 600
