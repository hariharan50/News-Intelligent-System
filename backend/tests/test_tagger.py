"""Tagger behaviour on real headlines seen in the launch feeds."""

import pytest

from app.processing.tagger import Tagger, get_tagger


@pytest.fixture(scope="module")
def tagger() -> Tagger:
    return get_tagger()


@pytest.mark.parametrize(
    ("title", "event_type"),
    [
        ("Federal Reserve issues FOMC statement", "rate_decision"),
        ("RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", "rate_decision"),
        ("RBI cuts repo rate by 25 bps to 5.25%", "rate_decision"),
        # Speculation and reports are not the decision itself.
        ("RBI may raise repo rate by 75-100 bps in current cycle: Report", "rate_outlook"),
        ("RBI's next rate hike will be of 0.50%: SBI Economists", "rate_outlook"),
        (
            "RBI to conduct 3-day Variable Rate Reverse Repo (VRRR) auction under LAF",
            "routine_operation",
        ),
        ("Auction of 91-Day, 182-Day and 364-Day Treasury Bills", "routine_operation"),
        ("Reserve Bank of India – Bulletin Weekly Statistical Supplement – Extract", "routine_operation"),
        ("RBI steps in to support rupee as currency nears record low", "currency_intervention"),
        ("RBI tightens forex derivative rules: What changes for hedging", "regulatory_change"),
        ("Adjudication Order in the matter of VCK Share and Stock Broking", "regulatory_order"),
        ("Sebi notifies new settlement rule; unveils formula, fast-track route", "regulatory_change"),
        ("SEBI Settlement Regulations 2026: New formula, ₹10 lakh fast-track", "regulatory_change"),
        ("DMart Q2 Results: Cons profit rises nearly 9% YoY to Rs 743 crore", "earnings_result"),
        ("India's CPI inflation eases to 3.1% in September", "inflation_data"),
        ("US stocks post weekly gains as earnings, inflation data on tap", None),
        ("Dow Jones| Nasdaq | US Stock Market Today | Highlights: US stocks post weekly gains as earnings, inflation data loom", None),
        ("Markets eye CPI inflation data next week", None),
        ("Jio Platforms said to set IPO price band, listing likely before Diwali", "ipo_listing"),
        ("Three Saudi nationals killed in Houthi missile attacks on Riyadh airport", "military_conflict"),
        ("GST Council Meeting Oct 2026: List of decisions taken", "policy_announcement"),
        ("Powerful magnitude 7.7 earthquake hits Panama", None),
    ],
)  # fmt: skip
def test_event_type(tagger: Tagger, title: str, event_type: str | None) -> None:
    assert tagger.tag(title=title).event_type == event_type


def test_event_type_comes_from_headline_not_excerpt(tagger: Tagger) -> None:
    tags = tagger.tag(
        title="Gold hits one-week high, heads for weekly gain",
        excerpt="Traders weigh the chance of a Fed rate cut amid the war in the Middle East.",
    )
    assert tags.event_type is None
    # ...but topics and entities still use the excerpt.
    assert "fed" in tags.entities
    assert {"metals", "monetary_policy", "geopolitics"} <= set(tags.topics)


def test_entities_aliases_and_case_sensitive_acronyms(tagger: Tagger) -> None:
    tags = tagger.tag(title="ITC, TCS, L&T and RIL top gainers as Sensex rallies")
    assert {"itc", "tcs", "larsen", "reliance", "sensex"} <= set(tags.entities)

    # Lower-case words that look like acronyms are not entities.
    assert tagger.tag(title="Investors are fed up with titan-sized valuations").entities == ()


def test_longer_alias_wins_and_both_indices_are_recognised(tagger: Tagger) -> None:
    tags = tagger.tag(title="Bank Nifty slips while Nifty 50 holds steady")
    assert {"bank_nifty", "nifty50"} <= set(tags.entities)


def test_source_implies_entity(tagger: Tagger) -> None:
    tags = tagger.tag(
        title="Auction of State Government Securities", source_slug="rbi-press-releases"
    )
    assert "rbi" in tags.entities
    assert "monetary_policy" in tags.topics  # implied by the RBI entity


def test_regions(tagger: Tagger) -> None:
    us = tagger.tag(title="Federal Reserve issues FOMC statement", source_region="US")
    assert us.regions == ("US",)

    india = tagger.tag(title="RBI cuts repo rate", source_region="GLOBAL")
    assert india.regions == ("IN",)

    nowhere = tagger.tag(title="Powerful earthquake hits Panama", source_region="GLOBAL")
    assert nowhere.regions == ("GLOBAL",)

    war = tagger.tag(title="Iran fires missiles at Israel", source_region="IN")
    assert set(war.regions) == {"IN", "IR", "IL"}


def test_event_implies_topic(tagger: Tagger) -> None:
    tags = tagger.tag(title="Hindalco Q2 results: net profit jumps 20%")
    assert tags.event_type == "earnings_result"
    assert "earnings" in tags.topics
    assert "hindalco" in tags.entities


def test_custom_taxonomy_is_injectable() -> None:
    from app.processing.taxonomy import Entity, EventType, Topic

    tagger = Tagger(
        topics=[Topic("weather", "Weather", (r"monsoon",))],
        entities=[Entity("imd", "IMD", "institution", ("India Meteorological Department",))],
        event_types=[EventType("forecast", "Forecast", (r"forecasts?",))],
        source_entities={},
    )
    tags = tagger.tag(title="India Meteorological Department forecasts normal monsoon")
    assert tags == tags.__class__(("weather",), ("imd",), "forecast", ("GLOBAL",))
