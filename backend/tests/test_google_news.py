"""Google News search feeds: publisher handling, freshness, and the feed showing the real
publisher (the integration test needs Docker PostgreSQL/Redis)."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from sqlalchemy import select

from app.ingestion.adapters import get_adapter
from app.ingestion.adapters.google_news import GoogleNewsAdapter, google_news_url, parse_google_news
from app.ingestion.service import build_article_rows, insert_articles
from app.models import Story
from app.models.source import SourceKind
from tests.test_profiles_feed_api import add_source, pipeline, sign_up

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def rss(*items: tuple[str, str, str, datetime]) -> bytes:
    body = "".join(
        f"""<item>
  <title>{title}</title>
  <link>https://news.google.com/rss/articles/{abs(hash(title))}?oc=5</link>
  <guid isPermaLink="false">{abs(hash(title))}</guid>
  <pubDate>{format_datetime(when)}</pubDate>
  <description>&lt;a href="https://news.google.com/x"&gt;{title}&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;{publisher}&lt;/font&gt;</description>
  <source url="{site}">{publisher}</source>
</item>"""
        for title, publisher, site, when in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"RBI" - Google News</title>{body}</channel></rss>""".encode()


FEED = rss(
    ("RBI opens dollar window for oil firms as rupee nears record low - Reuters", "Reuters",
     "https://www.reuters.com", NOW - timedelta(hours=1)),
    ("RBI tightens forex rules - The Economic Times", "The Economic Times",
     "https://economictimes.indiatimes.com", NOW - timedelta(hours=2)),
    ("RBI governor speech highlights - Business Standard", "Business Standard",
     "https://www.business-standard.com", NOW - timedelta(hours=3)),
    ("Old RBI story from last week - Moneycontrol", "Moneycontrol",
     "https://www.moneycontrol.com", NOW - timedelta(days=5)),
    ("RBI notes for exam aspirants - usthadian.com", "www.usthadian.com",
     "https://www.usthadian.com", NOW - timedelta(hours=1)),
    ("RBI rupee update - Moneycontrol.com", "Moneycontrol.com",
     "https://www.moneycontrol.com", NOW - timedelta(hours=4)),
)  # fmt: skip


def test_parse_keeps_publisher_strips_suffix_and_skips_direct_and_old() -> None:
    items = parse_google_news(FEED, "https://news.google.com/rss/search?q=RBI", now=NOW)
    assert [(i.title, i.publisher) for i in items] == [
        ("RBI opens dollar window for oil firms as rupee nears record low", "Reuters"),
        ("RBI governor speech highlights", "Business Standard"),
        ("RBI rupee update", "Moneycontrol.com"),  # untrusted exam-prep site dropped
    ]
    assert all(i.excerpt is None for i in items)  # Google's description is just a link back
    assert items[0].url.startswith("https://news.google.com/rss/articles/")


def test_search_url_is_limited_to_recent_results() -> None:
    url = google_news_url('"RBI" OR SEBI', edition="IN")
    assert url.startswith("https://news.google.com/rss/search?q=%22RBI%22+OR+SEBI+when%3A1d")
    assert url.endswith("&hl=en-IN&gl=IN&ceid=IN:en")
    assert "gl=US" in google_news_url("Fed", edition="US")
    assert isinstance(get_adapter(SourceKind.GOOGLE_NEWS), GoogleNewsAdapter)


def test_invalid_document_is_an_error() -> None:
    from app.ingestion.types import FetchError

    with pytest.raises(FetchError):
        parse_google_news(b"<html><body>blocked</body></html>", "https://news.google.com/rss")


@pytest.mark.usefixtures("db")
async def test_feed_shows_the_real_publisher(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    google = await add_source(
        db, "gnews-rbi", "https://news.google.com", name="Google News: RBI",
        kind="google_news", category="aggregator",
    )  # fmt: skip
    now = datetime.now(UTC)
    items = parse_google_news(
        rss(
            ("RBI keeps repo rate unchanged at 5.5%, maintains neutral stance - Reuters", "Reuters",
             "https://www.reuters.com", now - timedelta(minutes=30)),
            ("RBI keeps repo rate unchanged at 5.5% in policy review - Business Standard",
             "Business Standard", "https://www.business-standard.com", now - timedelta(minutes=20)),
        ),
        "https://news.google.com/rss/search?q=RBI",
        now=now,
    )  # fmt: skip
    async with db() as session, session.begin():
        await insert_articles(session, build_article_rows(google, items, now))
    await pipeline(db)

    async with db() as session:
        [story] = list(await session.scalars(select(Story)))
    # Two publishers through one Google search: two outlets, not one.
    assert story.source_count == 2 and story.lead_publisher in {"Reuters", "Business Standard"}

    feed = (await client.get("/api/feed", headers=headers)).json()
    [item] = feed["items"]
    assert item["lead_source"] in {"Reuters", "Business Standard"}
    assert {a["source"] for a in item["articles"]} == {"Reuters", "Business Standard"}
    assert item["outlets"] == ["Business Standard", "Reuters"]

    by_outlet = (
        await client.get("/api/feed", params={"outlet": "Reuters"}, headers=headers)
    ).json()
    assert by_outlet["total"] == 1
    none = (
        await client.get("/api/feed", params={"outlet": "Bloomberg.com"}, headers=headers)
    ).json()
    assert none["total"] == 0
