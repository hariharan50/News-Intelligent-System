import asyncio
import calendar
from datetime import UTC, datetime, tzinfo
from time import struct_time
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import httpx

from app.core.config import get_settings
from app.ingestion.http import conditional_get
from app.ingestion.normalize import (
    clean_excerpt,
    clean_title,
    html_to_text,
    parse_feed_date,
    repair_url,
)
from app.ingestion.types import FeedItem, FetchError, FetchResult, SourceRef

MAX_TAGS = 10
GUID_MAX_CHARS = 512


def _to_datetime(value: struct_time | None) -> datetime | None:
    # feedparser normalises parsed dates to UTC struct_time.
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    except (OverflowError, ValueError):
        return None


def _entry_date(entry, default_tz: tzinfo) -> tuple[datetime | None, bool]:  # noqa: ANN001
    """Prefer our own parse of the raw string: feedparser rejects common real-world formats
    and reads timezone-less dates as UTC. Fall back to feedparser's parse."""
    for key in ("published", "updated"):
        when, date_only = parse_feed_date(entry.get(key), default_tz)
        if when is not None:
            return when, date_only
    return _to_datetime(entry.get("published_parsed") or entry.get("updated_parsed")), False


def parse_feed(body: bytes, feed_url: str, default_tz: tzinfo = UTC) -> list[FeedItem]:
    """Parse RSS 2.0 / RSS 1.0 / Atom bytes. Items without a title or link are dropped."""
    parsed = feedparser.parse(body)
    if not parsed.entries:
        # An HTML page served with 200 (block page, consent wall) parses "successfully" with
        # no version; treat it as a failure so the source doesn't look healthy while empty.
        if parsed.bozo:
            raise FetchError(f"not a valid feed: {type(parsed.bozo_exception).__name__}")
        if not parsed.get("version"):
            raise FetchError("not a valid feed: no RSS/Atom document found")

    items: list[FeedItem] = []
    for entry in parsed.entries:
        title = clean_title(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        url = repair_url(urljoin(feed_url, link))
        guid = (entry.get("id") or url).strip()[:GUID_MAX_CHARS]
        tags = [
            t for t in (html_to_text(tag.get("term"))[:100] for tag in entry.get("tags", [])) if t
        ][:MAX_TAGS]
        published_at, date_only = _entry_date(entry, default_tz)
        items.append(
            FeedItem(
                guid=guid,
                url=url,
                title=title,
                excerpt=clean_excerpt(entry.get("summary"), title),
                author=(html_to_text(entry.get("author")) or None),
                tags=tags,
                published_at=published_at,
                published_date_only=date_only,
            )
        )
    return items


class RssAdapter:
    async def fetch(self, client: httpx.AsyncClient, source: SourceRef) -> FetchResult:
        settings = get_settings()
        resp = await conditional_get(
            client,
            source.url,
            etag=source.etag,
            last_modified=source.last_modified,
            max_bytes=settings.ingest_max_bytes,
        )
        if resp.status_code == 304:
            return FetchResult(
                http_status=304,
                items=[],
                not_modified=True,
                etag=source.etag,
                last_modified=source.last_modified,
            )
        # feedparser is synchronous and CPU-bound; keep it off the event loop.
        items = await asyncio.to_thread(
            parse_feed, resp.body, source.url, ZoneInfo(source.timezone)
        )
        return FetchResult(
            http_status=resp.status_code,
            items=items,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )
