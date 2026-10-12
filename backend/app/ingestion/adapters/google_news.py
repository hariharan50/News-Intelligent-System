"""Google News search feeds (news.google.com/rss/search?q=...).

One search = one source. Google News gathers headlines from thousands of publishers, which
also brings in outlets we can't read directly (Reuters, Bloomberg, Business Standard,
Moneycontrol...). Differences from a plain RSS feed:

  * every item names its real publisher (<source>): we keep it, so the app shows
    "Reuters", not "Google News";
  * titles end in " - Publisher": removed;
  * the description is only a link back to the same headline: dropped;
  * search results include old articles: only items from the last `max_age` are kept;
  * publishers we already poll directly are skipped (their own feed is faster and has an
    excerpt), so the same report doesn't arrive twice;
  * only publishers on TRUSTED_PUBLISHERS are kept: a Google search also returns blogs,
    exam-prep sites and local outlets, which are noise for a trader.

Links point to news.google.com and redirect to the publisher in the browser.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus, urljoin

import feedparser
import httpx

from app.core.config import get_settings
from app.ingestion.adapters.rss import GUID_MAX_CHARS, _entry_date
from app.ingestion.http import conditional_get
from app.ingestion.normalize import clean_title, html_to_text
from app.ingestion.types import FeedItem, FetchError, FetchResult, SourceRef

MAX_AGE = timedelta(hours=48)
MAX_ITEMS = 40  # newest first, per poll

# Publishers we read directly (see seed.py). Matched case-insensitively against the start
# of Google's publisher name, so "The Economic Times" and "Economic Times" both match.
DIRECT_PUBLISHERS = (
    "the economic times", "economic times", "mint", "livemint", "bbc", "cnbc", "cnbctv18",
    "the hindu", "businessline", "business today", "ndtv profit", "the wall street journal",
    "wsj", "marketwatch", "the new york times", "the guardian", "al jazeera", "investing.com",
    "reserve bank of india", "sebi", "federal reserve",
)  # fmt: skip


# Publishers whose Google News items we keep (names as Google gives them, normalised by
# _norm). Wires and major business media, India first. Add more here as needed.
TRUSTED_PUBLISHERS = frozenset({
    # Wires and global business media
    "reuters", "bloomberg", "financial times", "ft", "associated press", "ap news", "afp",
    "the economist", "barron's", "fortune", "forbes", "business insider", "axios", "politico",
    "yahoo finance", "cnn", "fox business", "nikkei asia", "south china morning post",
    "the japan times", "dw", "france 24", "sky news", "npr", "the washington post",
    "the hill", "oilprice", "kitco", "s&p global", "fxstreet",
    "investor's business daily", "benzinga", "euronews", "the independent",
    "the telegraph", "abc news", "cbs news", "nbc news", "time",
    # India
    "business standard", "moneycontrol", "financial express", "the financial express",
    "the times of india", "times of india", "hindustan times", "ndtv", "et now",
    "zee business", "india today", "the indian express", "indian express", "deccan herald",
    "news18", "fortune india", "outlook business", "bw businessworld", "theprint",
    "the print", "firstpost", "wion", "the quint", "scroll", "deccan chronicle",
    "press trust of india", "pti", "ani", "ians", "forbes india",
    "the week", "rbi", "pib",
})  # fmt: skip


def _norm(publisher: str) -> str:
    """'Moneycontrol.com' -> 'moneycontrol', 'Bloomberg.com' -> 'bloomberg'."""
    name = publisher.strip().lower()
    for suffix in (".com", ".in", ".co.in", ".co.uk", ".net"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.removeprefix("www.")


def _direct(publisher: str) -> bool:
    name = publisher.strip().lower()
    return any(name == p or name.startswith(p + " ") for p in DIRECT_PUBLISHERS)


def trusted(publisher: str | None) -> bool:
    return bool(publisher) and _norm(publisher) in TRUSTED_PUBLISHERS


def _strip_publisher(title: str, publisher: str | None) -> str:
    """'RBI holds rates - Reuters' -> 'RBI holds rates'."""
    if publisher:
        suffix = f" - {publisher}"
        if title.endswith(suffix):
            return title[: -len(suffix)].rstrip()
    head, sep, _ = title.rpartition(" - ")
    return head if sep and len(head) > 20 else title


def parse_google_news(body: bytes, feed_url: str, now: datetime | None = None) -> list[FeedItem]:
    parsed = feedparser.parse(body)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FetchError("not a valid Google News feed")
    now = now or datetime.now(UTC)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        source = entry.get("source") or {}
        publisher = html_to_text(source.get("title")) or None
        if not trusted(publisher) or _direct(publisher):
            continue
        title = clean_title(_strip_publisher(entry.get("title") or "", publisher))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        published_at, date_only = _entry_date(entry, UTC)
        if published_at is None or now - published_at > MAX_AGE:
            continue
        url = urljoin(feed_url, link)
        items.append(
            FeedItem(
                guid=(entry.get("id") or url).strip()[:GUID_MAX_CHARS],
                url=url,
                title=title,
                excerpt=None,
                publisher=publisher[:120] if publisher else None,
                published_at=published_at,
                published_date_only=date_only,
            )
        )
    items.sort(key=lambda i: i.published_at, reverse=True)
    return items[:MAX_ITEMS]


class GoogleNewsAdapter:
    async def fetch(self, client: httpx.AsyncClient, source: SourceRef) -> FetchResult:
        resp = await conditional_get(
            client,
            source.url,
            etag=source.etag,
            last_modified=source.last_modified,
            max_bytes=get_settings().ingest_max_bytes,
        )
        if resp.status_code == 304:
            return FetchResult(
                http_status=304, items=[], not_modified=True,
                etag=source.etag, last_modified=source.last_modified,
            )  # fmt: skip
        items = await asyncio.to_thread(parse_google_news, resp.body, source.url)
        return FetchResult(
            http_status=resp.status_code,
            items=items,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
        )


def google_news_url(query: str, *, edition: str = "IN", days: int = 1) -> str:
    """The RSS URL for a search, limited to the last `days` days.

    edition "IN" = Google News India (English), "US" = United States (English)."""
    hl, gl = ("en-IN", "IN") if edition == "IN" else ("en-US", "US")
    q = quote_plus(f"{query} when:{days}d")
    return f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:en"
