from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ingestion.normalize import (
    canonical_url,
    clean_excerpt,
    clean_title,
    html_to_text,
    normalise_title_for_hash,
    parse_feed_date,
    repair_url,
    resolve_published_at,
    truncate,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "HTTPS://WWW.LiveMint.com/market/story.html?utm_source=rss&utm_medium=feed#top",
            "https://www.livemint.com/market/story.html",
        ),
        ("https://example.com/a/?b=2&a=1&fbclid=xyz", "https://example.com/a?a=1&b=2"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_canonical_url(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


def test_canonical_url_same_article_from_two_feeds_matches() -> None:
    a = canonical_url("https://www.bbc.co.uk/news/articles/abc?at_medium=RSS&at_campaign=rss")
    b = canonical_url("https://www.bbc.co.uk/news/articles/abc")
    assert a == b


def test_html_to_text_strips_tags_entities_and_scripts() -> None:
    raw = "<p>RBI keeps <b>repo&nbsp;rate</b> at 5.5%</p><script>alert(1)</script><br>More&amp;more"
    assert html_to_text(raw) == "RBI keeps repo rate at 5.5% More&more"


def test_clean_excerpt_drops_text_that_repeats_the_title() -> None:
    title = clean_title("RBI holds rates")
    assert clean_excerpt("<p>RBI holds rates</p>", title) is None
    assert clean_excerpt("", title) is None
    assert clean_excerpt("Repo unchanged at 5.5%.", title) == "Repo unchanged at 5.5%."


def test_truncate_prefers_word_boundary() -> None:
    text = "word " * 50
    out = truncate(text.strip(), 40)
    assert len(out) <= 40
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")


def test_normalise_title_for_hash_ignores_case_punctuation_and_accents() -> None:
    assert normalise_title_for_hash("RBI  Holds Rates!") == normalise_title_for_hash(
        "rbi holds rates"
    )
    assert normalise_title_for_hash("Café") == normalise_title_for_hash("cafe")


IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("raw", "expected", "date_only"),
    [
        # RBI: RFC 822 without a timezone -> source-local time (IST).
        ("Sat, 10 Oct 2026 01:25:00", datetime(2026, 10, 9, 19, 55, tzinfo=UTC), False),
        # SEBI: no weekday, explicit offset.
        ("10 Oct 2026 14:00:02 +0530", datetime(2026, 10, 10, 8, 30, 2, tzinfo=UTC), False),
        # SEBI: date only, with a stray comma.
        ("09 Oct, 2026 +0530", datetime(2026, 10, 8, 18, 30, tzinfo=UTC), True),
        # Standard formats still work.
        ("Fri, 09 Oct 2026 18:00:00 GMT", datetime(2026, 10, 9, 18, 0, tzinfo=UTC), False),
        ("2026-10-09T18:00:00Z", datetime(2026, 10, 9, 18, 0, tzinfo=UTC), False),
        ("2026-10-09T23:30:00+05:30", datetime(2026, 10, 9, 18, 0, tzinfo=UTC), False),
    ],
)
def test_parse_feed_date(raw: str, expected: datetime, date_only: bool) -> None:
    assert parse_feed_date(raw, IST) == (expected, date_only)


@pytest.mark.parametrize("raw", [None, "", "   ", "yesterday-ish", "99 Foo 2026"])
def test_parse_feed_date_unparseable(raw: str | None) -> None:
    assert parse_feed_date(raw, IST) == (None, False)


def test_repair_url() -> None:
    broken = "https://www.sebi.gov.in/https://www.sebi.gov.in/sebi_data/x.pdf"
    assert repair_url(broken) == "https://www.sebi.gov.in/sebi_data/x.pdf"
    fine = "https://example.com/redirect?to=https%3A%2F%2Fother.example"
    assert repair_url(fine) == fine
    assert repair_url("  https://example.com/a  ") == "https://example.com/a"


def test_resolve_date_only_published_at() -> None:
    fetched = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    today_start = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)  # 10 Oct 00:00 IST
    earlier_day = datetime(2026, 10, 7, 18, 30, tzinfo=UTC)

    # Dated today: first seen now, so "now" is the best estimate.
    assert resolve_published_at(today_start, fetched, date_only=True) == (fetched, True)
    # Dated an earlier day: keep the date, still estimated.
    assert resolve_published_at(earlier_day, fetched, date_only=True) == (earlier_day, True)


def test_resolve_published_at() -> None:
    fetched = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    past = fetched - timedelta(hours=1)

    assert resolve_published_at(past, fetched) == (past, False)
    assert resolve_published_at(None, fetched) == (fetched, True)
    # Wrong-timezone feeds put items hours in the future: distrust them.
    assert resolve_published_at(fetched + timedelta(hours=5), fetched) == (fetched, True)
    # Small clock skew is tolerated.
    slight = fetched + timedelta(minutes=2)
    assert resolve_published_at(slight, fetched) == (slight, False)
    # Naive datetimes are treated as UTC.
    naive = datetime(2026, 10, 10, 11, 0)
    assert resolve_published_at(naive, fetched) == (naive.replace(tzinfo=UTC), False)
