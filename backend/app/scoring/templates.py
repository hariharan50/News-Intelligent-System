"""Ready-made profiles. A user's profile is a copy, so editing it never changes the template."""

from dataclasses import dataclass

from app.processing.taxonomy import ENTITIES
from app.scoring.engine import RuleSpec


@dataclass(frozen=True)
class Template:
    slug: str
    name: str
    description: str
    show_threshold: int
    alert_threshold: int
    rules: tuple[RuleSpec, ...]


def _inc(kind: str, value: str, weight: int) -> RuleSpec:
    return RuleSpec(kind=kind, value=value, mode="include", weight=weight)


def _pin(event_type: str, entity: str, min_score: int) -> RuleSpec:
    return RuleSpec(kind="event_type", value=event_type, entity=entity, mode="pin", weight=10,
                    min_score=min_score)  # fmt: skip


_NIFTY50 = tuple(e.slug for e in ENTITIES if e.kind == "company")

INDIAN_MARKETS_TRADER = Template(
    slug="indian_markets_trader",
    name="Indian markets trader",
    description=(
        "Central banks, rupee, crude oil, geopolitics, SEBI and Nifty 50 companies. "
        "RBI and Fed rate decisions always alert."
    ),
    show_threshold=50,
    alert_threshold=80,
    rules=(
        # Pins: these events alert no matter how they are worded.
        _pin("rate_decision", "rbi", 95),
        _pin("rate_decision", "fed", 85),
        # Institutions and macro instruments
        _inc("entity", "rbi", 9),
        _inc("entity", "fed", 8),
        _inc("entity", "sebi", 7),
        _inc("entity", "usd_inr", 8),
        _inc("entity", "brent", 8),
        _inc("entity", "crude_oil", 8),
        _inc("entity", "opec", 7),
        _inc("entity", "nifty50", 7),
        _inc("entity", "sensex", 6),
        _inc("entity", "bank_nifty", 7),
        _inc("entity", "india_vix", 6),
        _inc("entity", "finmin", 6),
        _inc("entity", "gst_council", 5),
        _inc("entity", "ecb", 4),
        _inc("entity", "gold", 4),
        # Topics
        _inc("topic", "monetary_policy", 8),
        _inc("topic", "currency", 7),
        _inc("topic", "energy", 7),
        _inc("topic", "inflation", 7),
        _inc("topic", "geopolitics", 6),
        _inc("topic", "market_regulation", 6),
        _inc("topic", "indian_equities", 6),
        _inc("topic", "economy", 6),
        _inc("topic", "fiscal", 5),
        _inc("topic", "bonds", 5),
        _inc("topic", "trade", 5),
        _inc("topic", "earnings", 4),
        _inc("topic", "banking", 3),
        _inc("topic", "global_markets", 3),
        # Event types that move Indian markets whoever is involved
        _inc("event_type", "rate_decision", 9),
        _inc("event_type", "currency_intervention", 8),
        _inc("event_type", "inflation_data", 8),
        _inc("event_type", "gdp_data", 7),
        _inc("event_type", "supply_disruption", 7),
        _inc("event_type", "military_conflict", 6),
        _inc("event_type", "sanctions", 6),
        _inc("event_type", "tariff_action", 6),
        # Keywords
        _inc("keyword", "repo rate", 9),
        _inc("keyword", "rate cut", 8),
        _inc("keyword", "rate hike", 8),
        _inc("keyword", "FII", 6),
        _inc("keyword", "FPI", 6),
        # Weak signal: Indian stories get a small lift, never enough on their own.
        _inc("region", "IN", 3),
        # Every Nifty 50 company
        *(_inc("entity", slug, 5) for slug in _NIFTY50),
    ),
)

TEMPLATES: dict[str, Template] = {t.slug: t for t in (INDIAN_MARKETS_TRADER,)}
DEFAULT_TEMPLATE = INDIAN_MARKETS_TRADER.slug
