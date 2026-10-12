from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.ingestion.adapters import FetchError, SourceRef
from app.ingestion.adapters.rss import RssAdapter, parse_feed
from app.ingestion.http import parse_retry_after

FEED_URL = "https://feed.example/rss.xml"

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Example</title>
  <item>
    <title>RBI keeps repo rate unchanged at 5.5%</title>
    <link>https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1</link>
    <guid isPermaLink="false">prid-1</guid>
    <description><![CDATA[<p>The MPC voted <b>unanimously</b>.</p>]]></description>
    <pubDate>Fri, 09 Oct 2026 05:30:00 GMT</pubDate>
    <category>Monetary Policy</category>
  </item>
  <item><title>Item without a link is dropped</title></item>
  <item>
    <title>Relative link</title>
    <link>/news/2</link>
    <pubDate>Fri, 09 Oct 2026 11:00:00 +0530</pubDate>
  </item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Fed</title>
  <entry>
    <title>Federal Reserve issues FOMC statement</title>
    <link href="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261009a.htm"/>
    <id>tag:federalreserve.gov,2026:monetary20261009a</id>
    <updated>2026-10-09T18:00:00Z</updated>
    <summary>The Committee decided to lower the target range.</summary>
  </entry>
</feed>"""


def test_parse_rss() -> None:
    items = parse_feed(RSS, FEED_URL)

    assert [i.title for i in items] == ["RBI keeps repo rate unchanged at 5.5%", "Relative link"]
    first, second = items
    assert first.guid == "prid-1"
    assert first.excerpt == "The MPC voted unanimously."
    assert first.tags == ["Monetary Policy"]
    assert first.published_at == datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    # Relative links resolve against the feed URL; with no <guid>, the link is the guid.
    assert second.url == "https://feed.example/news/2"
    assert second.guid == "https://feed.example/news/2"
    # +0530 is converted to UTC.
    assert second.published_at == datetime(2026, 10, 9, 5, 30, tzinfo=UTC)


def test_parse_atom() -> None:
    [item] = parse_feed(ATOM, FEED_URL)
    assert item.guid == "tag:federalreserve.gov,2026:monetary20261009a"
    assert item.url.endswith("monetary20261009a.htm")
    assert item.excerpt == "The Committee decided to lower the target range."
    assert item.published_at == datetime(2026, 10, 9, 18, 0, tzinfo=UTC)


def test_parse_real_world_rbi_and_sebi_quirks() -> None:
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
      <item><title>RBI press release</title>
        <link>https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=63784</link>
        <pubDate>Fri, 09 Oct 2026 19:20:00</pubDate></item>
      <item><title>SEBI order</title>
        <link>https://www.sebi.gov.in/https://www.sebi.gov.in/sebi_data/order.pdf</link>
        <pubDate>09 Oct, 2026 +0530</pubDate></item>
    </channel></rss>"""

    rbi, sebi = parse_feed(feed, FEED_URL, ZoneInfo("Asia/Kolkata"))

    assert rbi.published_at == datetime(2026, 10, 9, 13, 50, tzinfo=UTC)  # 19:20 IST
    assert not rbi.published_date_only
    assert sebi.url == "https://www.sebi.gov.in/sebi_data/order.pdf"
    assert sebi.published_date_only


def test_parse_rejects_html_page() -> None:
    with pytest.raises(FetchError, match="not a valid feed"):
        parse_feed(b"<html><body>Access denied</body></html>", FEED_URL)


def test_parse_accepts_valid_empty_feed() -> None:
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    assert parse_feed(empty, FEED_URL) == []


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_uses_conditional_get_and_handles_304() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=RSS, headers={"ETag": '"v1"'})

    adapter = RssAdapter()
    async with _client(handler) as client:
        first = await adapter.fetch(client, SourceRef(url=FEED_URL))
        second = await adapter.fetch(client, SourceRef(url=FEED_URL, etag=first.etag))

    assert first.http_status == 200 and len(first.items) == 2 and first.etag == '"v1"'
    assert second.not_modified and second.items == [] and second.etag == '"v1"'
    assert "If-None-Match" not in seen_headers[0]


async def test_fetch_429_carries_retry_after() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    async with _client(handler) as client:
        with pytest.raises(FetchError) as info:
            await RssAdapter().fetch(client, SourceRef(url=FEED_URL))
    assert info.value.http_status == 429
    assert info.value.retry_after == 120


async def test_fetch_403_is_an_error_without_retry_after() -> None:
    async with _client(lambda _: httpx.Response(403, text="denied")) as client:
        with pytest.raises(FetchError) as info:
            await RssAdapter().fetch(client, SourceRef(url=FEED_URL))
    assert info.value.http_status == 403
    assert info.value.retry_after is None


async def test_fetch_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ingest_max_bytes", 100)
    async with _client(lambda _: httpx.Response(200, content=RSS)) as client:
        with pytest.raises(FetchError, match="larger than 100 bytes"):
            await RssAdapter().fetch(client, SourceRef(url=FEED_URL))


async def test_fetch_timeout_becomes_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as client:
        with pytest.raises(FetchError, match="timeout"):
            await RssAdapter().fetch(client, SourceRef(url=FEED_URL))


def test_parse_retry_after() -> None:
    assert parse_retry_after("30") == 30
    assert parse_retry_after("999999") == 3600
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0  # in the past
