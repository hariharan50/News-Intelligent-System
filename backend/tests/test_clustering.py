"""Clustering decisions on real headline pairs from the launch feeds."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.normalize import normalise_title_for_hash, sha256
from app.processing.clustering import (
    ArticleFeatures,
    ClusterIndex,
    dice,
    match_pair,
    significant_words,
)
from app.processing.tagger import get_tagger

T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


def features(
    title: str,
    publisher: str,
    *,
    at: datetime = T0,
    story: uuid.UUID | None = None,
    source_slug: str | None = None,
) -> ArticleFeatures:
    tags = get_tagger().tag(title=title, source_slug=source_slug)
    return ArticleFeatures(
        article_id=uuid.uuid4(),
        publisher=publisher,
        title_hash=sha256(normalise_title_for_hash(title)),
        words=significant_words(title),
        entities=frozenset(tags.entities),
        event_type=tags.event_type,
        published_at=at,
        story_id=story,
    )


def placed(title: str, publisher: str, **kw) -> ArticleFeatures:
    return features(title, publisher, story=uuid.uuid4(), **kw)


def test_significant_words_drop_stop_words_and_stem() -> None:
    words = significant_words("Airtel Money debuts on LSE in the biggest listing in 5 years")
    assert words == {"airtel", "money", "debut", "lse", "biggest", "list", "5", "year"}


def test_significant_words_keep_numbers_and_currency() -> None:
    assert {"₹742.98", "8.5%", "q2"} <= significant_words("Q2: profit jumps 8.5% to ₹742.98 crore")


def test_dice() -> None:
    assert dice(frozenset("ab"), frozenset("ab")) == 1.0
    assert dice(frozenset("ab"), frozenset("cd")) == 0.0
    assert dice(frozenset(), frozenset("a")) == 0.0


SAME_EVENT = [
    (
        "Airtel Money IPO: London's biggest listing in 5 years makes debut",
        "Airtel Money debuts on LSE in biggest listing in 5 years",
    ),
    (
        "Avenue Supermarts Q2 Results: DMart parent net profit jumps 8.5% to ₹742.98 crore",
        "DMart Q2 Results: Cons profit rises nearly 9% YoY to Rs 743 crore, revenue jumps 18%",
    ),
    (
        "GST Council meet: Tax officers' arrest power scrapped; easier e-comm norms",
        "GST Council meet: Scrapping tax officers' arrest powers, decriminalisation",
    ),
    (
        "SEBI Settlement Regulations 2026: New formula, ₹10 lakh fast-track, and other changes",
        "Sebi notifies new settlement rule; unveils formula, fast-track route",
    ),
    (
        "RBI to conduct Rs 25,000 cr OMO sale auction of government securities on Oct 13",
        "RBI announces OMO Sale of Government of India Securities",
    ),
]

DIFFERENT_EVENTS = [
    (
        "RBI to conduct Rs 25,000 cr OMO sale auction of government securities on Oct 13",
        "Auction of State Government Securities",
    ),
    (
        "RBI's next rate hike will be of 0.50%: SBI Economists",
        "RBI rate hike will not stem outflows, leaving central bank in a bind",
    ),
    (
        "Avenue Supermarts Q2 Results: DMart parent net profit jumps 8.5%",
        "Poonawalla Fincorp Q2 Results: Profit skyrockets 407% YoY to Rs 375 crore",
    ),
]


@pytest.mark.parametrize(("a", "b"), SAME_EVENT)
def test_same_event_from_two_publishers_joins(a: str, b: str) -> None:
    other = placed(b, "economictimes.indiatimes.com")
    match = match_pair(features(a, "livemint.com"), other)
    assert match is not None and match.story_id == other.story_id


@pytest.mark.parametrize(("a", "b"), DIFFERENT_EVENTS)
def test_different_events_stay_apart(a: str, b: str) -> None:
    assert match_pair(features(a, "livemint.com"), placed(b, "rbi.org.in")) is None


def test_same_publisher_only_merges_identical_headlines() -> None:
    day1 = placed("Money Market Operations as on October 09, 2026", "rbi.org.in")
    day2 = features("Money Market Operations as on October 10, 2026", "rbi.org.in")
    assert dice(day1.words, day2.words) > 0.8  # very similar words...
    assert match_pair(day2, day1) is None  # ...but a different release

    again = features("Money Market Operations as on October 09, 2026", "rbi.org.in")
    match = match_pair(again, day1)
    assert match is not None and match.rule == "same_title"


def test_specific_event_joins_despite_different_wording() -> None:
    official = placed(
        "Federal Reserve issues FOMC statement", "federalreserve.gov", source_slug="fed-press"
    )
    press = features("Fed cuts interest rates by a quarter point", "bbc.co.uk")
    match = match_pair(press, official)
    assert match is not None and match.rule == "same_specific_event"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "Fed cuts interest rates by a quarter point",
            "ECB cuts interest rates by a quarter point",
        ),
        (
            "HDFC Bank Q2 results: net profit rises 10%, beats estimates",
            "ICICI Bank Q2 results: net profit rises 10%, beats estimates",
        ),
    ],
)
def test_near_identical_wording_about_different_actors_stays_apart(a: str, b: str) -> None:
    first, second = placed(a, "bbc.co.uk"), features(b, "livemint.com")
    assert dice(first.words, second.words) >= 0.55  # word similarity alone would merge them
    assert match_pair(second, first) is None


def test_specific_event_window_is_one_day() -> None:
    official = placed(
        "Federal Reserve issues FOMC statement", "federalreserve.gov", source_slug="fed-press"
    )
    late = features(
        "Fed cuts interest rates by a quarter point", "bbc.co.uk", at=T0 + timedelta(hours=30)
    )
    assert match_pair(late, official) is None


def test_outside_window_never_joins() -> None:
    old = placed("Gold hits one-week high", "livemint.com", at=T0 - timedelta(hours=49))
    assert match_pair(features("Gold hits one-week high", "economictimes.com"), old) is None


def test_index_prefers_the_strongest_rule() -> None:
    index = ClusterIndex()
    weak = placed("RBI announces OMO Sale of Government of India Securities", "rbi.org.in")
    exact = placed(
        "RBI to conduct Rs 25,000 cr OMO sale auction of government securities", "moneycontrol.com"
    )
    index.add(weak)
    index.add(exact)

    match = index.find(
        features(
            "RBI to conduct Rs 25,000 cr OMO sale auction of government securities",
            "livemint.com",
        )
    )
    assert match is not None and match.story_id == exact.story_id
    assert match.rule == "same_title"
