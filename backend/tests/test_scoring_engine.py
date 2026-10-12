"""Scoring engine, including the worked example from the project document (section 12)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.scoring.engine import (
    CompiledProfile,
    RuleSpec,
    StoryFacts,
    importance_factor,
    score_story,
    tier_of,
)
from app.scoring.templates import INDIAN_MARKETS_TRADER

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def story(**kw) -> StoryFacts:
    defaults = {
        "id": uuid.uuid4(),
        "topics": frozenset(),
        "entities": frozenset(),
        "event_type": None,
        "regions": frozenset({"IN"}),
        "source_slugs": frozenset({"mint-markets"}),
        "match_text": "",
        "max_authority": 3,
        "source_count": 1,
        "is_routine": False,
        "has_official_source": False,
        "published_at": NOW,
    }
    values = defaults | kw
    for key in ("topics", "entities", "regions", "source_slugs"):
        values[key] = frozenset(values[key])
    return StoryFacts(**values)


@pytest.fixture
def trader() -> CompiledProfile:
    t = INDIAN_MARKETS_TRADER
    return CompiledProfile(
        id=None,
        version=1,
        rules=list(t.rules),
        show_threshold=t.show_threshold,
        alert_threshold=t.alert_threshold,
        source_names={"rbi-press-releases": "RBI Press Releases"},
    )


# --------------------------------------------------------------------------- doc example

RBI_DECISION = story(
    topics={"monetary_policy", "bonds"},
    entities={"rbi", "india"},
    event_type="rate_decision",
    source_slugs={"rbi-press-releases"},
    match_text="rbi keeps repo rate unchanged at 5.5%",
    max_authority=5,
    has_official_source=True,
)
BRENT_JUMP = story(
    topics={"energy"},
    entities={"brent"},
    event_type="supply_disruption",
    regions={"GLOBAL", "ME"},
    match_text="brent crude jumps after supply disruption",
    max_authority=3,
    source_count=3,
)
RBI_ROUTINE = story(
    topics={"monetary_policy", "bonds"},
    entities={"rbi"},
    event_type="routine_operation",
    is_routine=True,
    match_text="rbi to conduct 3-day variable rate reverse repo (vrrr) auction",
    max_authority=5,
    has_official_source=True,
)
CRICKET = story(match_text="ipl auction: record bid for star all-rounder", max_authority=3)


def test_rbi_rate_decision_is_pinned_critical_and_alerts(trader: CompiledProfile) -> None:
    result = score_story(trader, RBI_DECISION, NOW)
    assert result is not None
    assert result.pinned and result.score == 95
    assert result.tier == "critical"
    assert result.score >= trader.alert_threshold
    assert result.reasons[0].label == "Pinned: Reserve Bank of India interest-rate decision"
    labels = [r.label for r in result.reasons]
    assert "Entity: Reserve Bank of India" in labels
    assert "Keyword: repo rate" in labels
    assert "Official source" in labels


def test_brent_supply_shock_is_high_but_below_alert_line(trader: CompiledProfile) -> None:
    result = score_story(trader, BRENT_JUMP, NOW)
    assert result is not None and not result.pinned
    assert result.tier == "high"
    assert trader.show_threshold <= result.score < trader.alert_threshold
    assert "3 sources" in [r.label for r in result.reasons]


def test_routine_rbi_auction_is_shown_low(trader: CompiledProfile) -> None:
    result = score_story(trader, RBI_ROUTINE, NOW)
    assert result is not None
    assert result.tier == "normal"
    assert trader.show_threshold <= result.score < 60
    assert "Routine release" in [r.label for r in result.reasons]


def test_region_only_story_is_hidden(trader: CompiledProfile) -> None:
    result = score_story(trader, CRICKET, NOW)
    assert result is not None
    assert result.relevance <= 25
    assert result.score < 20
    assert result.tier is None


def test_doc_example_ordering(trader: CompiledProfile) -> None:
    scores = [
        score_story(trader, s, NOW).score for s in (RBI_DECISION, BRENT_JUMP, RBI_ROUTINE, CRICKET)
    ]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------- mechanics


def profile(*rules: RuleSpec) -> CompiledProfile:
    return CompiledProfile(id=None, version=1, rules=list(rules))


def test_no_match_returns_none() -> None:
    assert score_story(profile(RuleSpec("entity", "rbi", weight=9)), CRICKET, NOW) is None


def test_more_matches_reinforce() -> None:
    one = profile(RuleSpec("topic", "energy", weight=6))
    two = profile(RuleSpec("topic", "energy", weight=6), RuleSpec("entity", "brent", weight=6))
    assert score_story(two, BRENT_JUMP, NOW).relevance > score_story(one, BRENT_JUMP, NOW).relevance


def test_one_strong_match_is_enough() -> None:
    result = score_story(profile(RuleSpec("entity", "brent", weight=10)), BRENT_JUMP, NOW)
    assert result.relevance == 100


def test_importance_can_halve_but_never_lift() -> None:
    weak = story(
        entities={"brent"}, max_authority=1, event_type="routine_operation", is_routine=True
    )
    strong = story(entities={"brent"}, max_authority=5, event_type="rate_decision", source_count=4)
    rule = profile(RuleSpec("entity", "brent", weight=10))
    assert score_story(rule, weak, NOW).score == 50
    assert score_story(rule, strong, NOW).score == 100
    factor, _ = importance_factor(strong)
    assert factor == 1.0


def test_exclude_hides_unless_pinned() -> None:
    mute = RuleSpec("entity", "brent", mode="exclude")
    include = RuleSpec("topic", "energy", weight=8)
    assert score_story(profile(include, mute), BRENT_JUMP, NOW).excluded

    pin = RuleSpec("event_type", "supply_disruption", mode="pin", min_score=90)
    result = score_story(profile(include, mute, pin), BRENT_JUMP, NOW)
    assert not result.excluded and result.score == 90


def test_expired_mute_no_longer_applies() -> None:
    include = RuleSpec("topic", "energy", weight=8)
    mute = RuleSpec("entity", "brent", mode="exclude", expires_at=NOW - timedelta(minutes=1))
    assert not score_story(profile(include, mute), BRENT_JUMP, NOW).excluded


def test_pinned_event_requires_the_named_entity() -> None:
    pin = RuleSpec("event_type", "rate_decision", entity="rbi", mode="pin", min_score=95)
    fed_decision = story(entities={"fed"}, event_type="rate_decision", regions={"US"})
    assert score_story(profile(pin), fed_decision, NOW) is None


def test_keyword_matches_whole_words_case_insensitively() -> None:
    rule = profile(RuleSpec("keyword", "FII", weight=6))
    assert score_story(rule, story(match_text="fii outflows continue for eighth week"), NOW)
    assert score_story(rule, story(match_text="the fiitjee results are out"), NOW) is None


@pytest.mark.parametrize(
    ("score", "tier"), [(100, "critical"), (80, "critical"), (79, "high"), (60, "high"),
                        (59, "normal"), (40, "normal"), (39, "low"), (20, "low"), (19, None)],
)  # fmt: skip
def test_tiers(score: int, tier: str | None) -> None:
    assert tier_of(score) == tier


# --------------------------------------------------------------------------- AI adjustment


def test_ai_adjustment_moves_score_but_not_rule_score(trader) -> None:
    plain = score_story(trader, RBI_DECISION, NOW)
    lowered = score_story(
        trader,
        story(
            **{**_facts_dict(RBI_DECISION), "ai_adjustment": -15, "ai_label": "minor market impact"}
        ),
        NOW,
    )
    # The RBI rate decision is pinned at 95: a pin's floor holds whatever AI says, and an
    # adjustment that changed nothing isn't shown as a reason.
    assert lowered.score == plain.score == lowered.alert_score
    assert lowered.rule_score is None
    assert all(r.kind != "ai" for r in lowered.reasons)


def test_ai_adjustment_on_unpinned_story(trader) -> None:
    crude = story(
        topics={"commodities"},
        entities={"crude_oil"},
        event_type="supply_disruption",
        source_count=2,
    )
    plain = score_story(trader, crude, NOW)
    raised = score_story(
        trader,
        story(**{**_facts_dict(crude), "ai_adjustment": 8, "ai_label": "major market impact"}),
        NOW,
    )
    assert raised.score == min(100, plain.score + 8)
    assert raised.rule_score == plain.score and raised.alert_score == plain.score
    assert raised.reasons[-1].label == f"AI: major market impact (+{raised.score - plain.score})"
    assert plain.rule_score is None  # no AI: nothing extra stored


def _facts_dict(f: StoryFacts) -> dict:
    return {name: getattr(f, name) for name in StoryFacts.__slots__}
