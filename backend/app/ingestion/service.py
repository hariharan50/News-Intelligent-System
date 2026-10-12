"""Polling orchestration: which sources are due, and what one poll does.

Flow (every `ingest_dispatch_interval_seconds`):
  dispatcher --claims due sources in PostgreSQL--> enqueues poll_source(id) per source
  poll_source --> lock source --> wait for host slot --> adapter.fetch --> store new articles
              --> update source health + schedule next poll --> log the attempt

Safety properties:
  * A source is claimed with FOR UPDATE SKIP LOCKED and a lease, so two dispatchers never
    hand out the same poll, and a poll lost to a crash is retried after the lease.
  * Articles are inserted with ON CONFLICT DO NOTHING, so re-polling, redelivered tasks
    and overlapping feeds never create duplicates.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.ingestion.adapters import FeedItem, FetchError, SourceAdapter, SourceRef, get_adapter
from app.ingestion.normalize import (
    canonical_url,
    normalise_title_for_hash,
    resolve_published_at,
    sha256,
    truncate,
)
from app.models import Article, FetchOutcome, Source, SourceFetch, SourceStatus

log = logging.getLogger(__name__)

ERROR_MAX_CHARS = 500
HOST_SLOT_MAX_WAIT_SECONDS = 15.0

# Timezone assumed for feed dates that don't state one, by source region.
REGION_TIMEZONES = {"IN": "Asia/Kolkata", "US": "America/New_York"}


@dataclass(frozen=True, slots=True)
class PollResult:
    outcome: FetchOutcome | None  # None when the poll was skipped
    items_seen: int = 0
    items_new: int = 0
    error: str | None = None


# --------------------------------------------------------------------------- scheduling


def next_poll_delay(
    interval: int,
    failures: int,
    *,
    retry_after: int | None = None,
    max_backoff: int | None = None,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds until the next poll.

    Success (failures == 0): the source's interval, with +/-10% jitter for intervals of a
    minute or more so sources don't all fire on the same tick.
    Failure: exponential back-off (interval * 2^failures) capped at max_backoff, with
    jitter, and never sooner than a server's Retry-After.
    """
    max_backoff = max_backoff or get_settings().ingest_max_backoff_seconds
    if failures <= 0:
        if interval < 60:
            return float(interval)
        return interval * (0.9 + 0.2 * rng())
    backoff = min(interval * (2 ** min(failures, 16)), max_backoff) * (0.8 + 0.4 * rng())
    if retry_after is not None:
        backoff = max(backoff, float(retry_after))
    return min(backoff, float(max_backoff))


def status_after_failure(failures: int) -> SourceStatus:
    return (
        SourceStatus.FAILING
        if failures >= get_settings().ingest_failing_after
        else SourceStatus.DEGRADED
    )


async def claim_due_sources(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 50
) -> list[uuid.UUID]:
    """Atomically take up to `limit` due sources, pushing their next_poll_at out by the lease."""
    lease = get_settings().ingest_claim_lease_seconds
    async with session_factory() as session, session.begin():
        result = await session.execute(
            text(
                """
                UPDATE sources
                   SET next_poll_at = now() + make_interval(secs => :lease)
                 WHERE id IN (
                       SELECT id FROM sources
                        WHERE enabled AND next_poll_at <= now()
                        ORDER BY next_poll_at
                        LIMIT :limit
                        FOR UPDATE SKIP LOCKED)
             RETURNING id
                """
            ),
            {"lease": lease, "limit": limit},
        )
        return list(result.scalars())


# --------------------------------------------------------------------------- rate limiting


async def wait_for_host_slot(redis: Redis, host: str, gap_ms: int) -> None:
    """Allow at most one request per `gap_ms` to a host, across every worker process."""
    key = f"nis:ratelimit:host:{host}"
    deadline = time.monotonic() + HOST_SLOT_MAX_WAIT_SECONDS
    while not await redis.set(key, "1", nx=True, px=gap_ms):
        if time.monotonic() > deadline:
            log.warning("host slot wait exceeded for %s; proceeding", host)
            return
        ttl_ms = await redis.pttl(key)
        await asyncio.sleep(max(ttl_ms, 50) / 1000)


# --------------------------------------------------------------------------- storage


def build_article_rows(
    source_id: uuid.UUID, items: list[FeedItem], fetched_at: datetime
) -> list[dict]:
    """Normalise items into insertable rows, dropping duplicates within this batch."""
    rows: list[dict] = []
    seen_guids: set[str] = set()
    seen_urls: set[bytes] = set()
    for item in items:
        url = canonical_url(item.url)
        url_hash = sha256(url)
        if item.guid in seen_guids or url_hash in seen_urls:
            continue
        seen_guids.add(item.guid)
        seen_urls.add(url_hash)
        published_at, estimated = resolve_published_at(
            item.published_at, fetched_at, date_only=item.published_date_only
        )
        rows.append(
            {
                "source_id": source_id,
                "guid": item.guid,
                "url": url,
                "url_hash": url_hash,
                "title": item.title,
                "title_hash": sha256(normalise_title_for_hash(item.title)),
                "excerpt": item.excerpt,
                "author": truncate(item.author, 300) if item.author else None,
                "publisher": item.publisher,
                "feed_tags": item.tags,
                "published_at": published_at,
                "published_at_estimated": estimated,
                "fetched_at": fetched_at,
            }
        )
    return rows


async def insert_articles(session: AsyncSession, rows: list[dict]) -> int:
    """Insert rows, silently skipping any that already exist. Returns how many were new."""
    if not rows:
        return 0
    stmt = pg_insert(Article).values(rows).on_conflict_do_nothing().returning(Article.id)
    result = await session.execute(stmt)
    return len(result.scalars().all())


# --------------------------------------------------------------------------- polling


async def poll_source(
    source_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    redis: Redis,
    adapter_for: Callable[[str], SourceAdapter] = get_adapter,
) -> PollResult:
    settings = get_settings()

    # A redelivered or duplicate task must not poll the same source concurrently.
    lock = redis.lock(
        f"nis:lock:poll:{source_id}", timeout=settings.ingest_claim_lease_seconds, blocking=False
    )
    if not await lock.acquire():
        log.info("poll skipped, already running: %s", source_id)
        return PollResult(outcome=None)

    try:
        async with session_factory() as session:
            source = await session.get(Source, source_id)
            if source is None or not source.enabled:
                return PollResult(outcome=None)
            kind, slug = source.kind, source.slug
            ref = SourceRef(
                url=source.url,
                etag=source.etag,
                last_modified=source.last_modified,
                timezone=REGION_TIMEZONES.get(source.region, "UTC"),
            )

        await wait_for_host_slot(
            redis, urlsplit(ref.url).hostname or ref.url, settings.ingest_host_min_gap_ms
        )

        started_at = datetime.now(UTC)
        t0 = time.monotonic()
        fetch_error: FetchError | None = None
        result = None
        try:
            result = await adapter_for(kind).fetch(client, ref)
        except FetchError as exc:
            fetch_error = exc
        except Exception as exc:  # an adapter bug must not kill the worker or stall the source
            log.exception("unexpected error polling %s", slug)
            fetch_error = FetchError(f"internal error: {type(exc).__name__}: {exc}")
        duration_ms = int((time.monotonic() - t0) * 1000)

        async with session_factory() as session, session.begin():
            source = await session.get(Source, source_id, with_for_update=True)
            if source is None:
                return PollResult(outcome=None)
            now = datetime.now(UTC)
            source.last_polled_at = now

            if fetch_error is not None:
                source.consecutive_failures += 1
                source.status = status_after_failure(source.consecutive_failures)
                source.last_error = truncate(str(fetch_error), ERROR_MAX_CHARS)
                delay = next_poll_delay(
                    source.poll_interval_seconds,
                    source.consecutive_failures,
                    retry_after=fetch_error.retry_after,
                )
                source.next_poll_at = now + timedelta(seconds=delay)
                session.add(
                    SourceFetch(
                        source_id=source_id,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        outcome=FetchOutcome.ERROR,
                        http_status=fetch_error.http_status,
                        error=source.last_error,
                    )
                )
                log.warning(
                    "poll failed: %s (%d in a row, retry in %.0fs): %s",
                    slug,
                    source.consecutive_failures,
                    delay,
                    source.last_error,
                )
                return PollResult(outcome=FetchOutcome.ERROR, error=source.last_error)

            assert result is not None
            rows = build_article_rows(source_id, result.items, started_at)
            items_new = await insert_articles(session, rows)
            outcome = FetchOutcome.NOT_MODIFIED if result.not_modified else FetchOutcome.OK

            source.consecutive_failures = 0
            source.status = SourceStatus.HEALTHY
            source.last_error = None
            source.last_success_at = now
            if items_new:
                source.last_new_item_at = now
            if not result.not_modified:
                source.etag = result.etag
                source.last_modified = result.last_modified
            source.next_poll_at = now + timedelta(
                seconds=next_poll_delay(source.poll_interval_seconds, 0)
            )
            session.add(
                SourceFetch(
                    source_id=source_id,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    outcome=outcome,
                    http_status=result.http_status,
                    items_seen=len(result.items),
                    items_new=items_new,
                )
            )
            log.info(
                "polled %s: %s, %d items, %d new, %dms",
                slug,
                outcome.value,
                len(result.items),
                items_new,
                duration_ms,
            )
            return PollResult(outcome=outcome, items_seen=len(result.items), items_new=items_new)
    finally:
        try:
            await lock.release()
        except Exception:  # lock expired or already released; nothing to do
            pass
