"""Score one story for one profile. Pure functions: no I/O, easy to test and explain.

    score = relevance x importance_factor        (then pins and excludes apply)

relevance (0-100): how well the story matches the profile's include rules. Matches combine
    like independent signals ("noisy-OR"): 1 - prod(1 - p_i), where p_i = weight/10 x a
    per-kind factor. Several matches reinforce each other; one strong match is enough.
    If only weak kinds matched (region, source), relevance is capped at 25: "it is from
    India" alone never puts a story in the feed.

importance_factor (0.5-1.0): how big the story is for anyone: source authority, event type
    and how many outlets carry it. It can halve a relevant story's score, but it multiplies
    relevance, so it can never lift an irrelevant story.

pins: a matching pin rule guarantees at least its min_score (e.g. RBI rate decision >= 95).
excludes: a matching exclude/mute hides the story, unless a pin also matches.

AI adjustment (optional): Claude's market-impact rating may move the score by a bounded
amount (default at most 15 points). It changes ranking and what reaches a feed, but alerts
use `rule_score`, the score before AI, so AI can never cause (or suppress) an alert.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property

from app.processing.taxonomy import (
    ENTITIES_BY_SLUG,
    EVENT_TYPES_BY_SLUG,
    TOPICS_BY_SLUG,
)

KIND_FACTOR = {
    "entity": 1.0,
    "event_type": 0.95,
    "keyword": 0.9,
    "topic": 0.85,
    "source": 0.6,
    "region": 0.35,
}
WEAK_KINDS = frozenset({"source", "region"})
WEAK_ONLY_RELEVANCE_CAP = 25

# How significant each event type is, regardless of who reads it (0..1).
EVENT_SIGNIFICANCE = {
    "rate_decision": 1.0,
    "currency_intervention": 0.85,
    "military_conflict": 0.8,
    "inflation_data": 0.8,
    "gdp_data": 0.8,
    "sanctions": 0.7,
    "tariff_action": 0.7,
    "regulatory_change": 0.7,
    "supply_disruption": 0.6,
    "economic_data": 0.6,
    "rating_action": 0.6,
    "earnings_result": 0.55,
    "policy_announcement": 0.55,
    "merger_acquisition": 0.5,
    "ipo_listing": 0.45,
    "rate_outlook": 0.4,
    "corporate_action": 0.3,
    "regulatory_order": 0.15,
    "routine_operation": 0.0,
}
DEFAULT_EVENT_SIGNIFICANCE = 0.15  # no recognised event: commentary, notices, analysis

# What happened matters most. Authority is a small lift: official sources also publish
# many minor notices (working papers, circular amendments), so "official" != "important".
W_AUTHORITY, W_EVENT, W_COVERAGE = 0.1, 0.75, 0.15

TIERS = (("critical", 80), ("high", 60), ("normal", 40), ("low", 20))


def tier_of(score: int) -> str | None:
    for name, floor in TIERS:
        if score >= floor:
            return name
    return None


@dataclass(frozen=True, slots=True)
class RuleSpec:
    kind: str
    value: str
    mode: str = "include"
    weight: int = 5
    entity: str | None = None
    min_score: int | None = None
    expires_at: datetime | None = None
    id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class StoryFacts:
    id: uuid.UUID
    topics: frozenset[str]
    entities: frozenset[str]
    event_type: str | None
    regions: frozenset[str]
    source_slugs: frozenset[str]
    match_text: str
    max_authority: int
    source_count: int
    is_routine: bool
    has_official_source: bool
    published_at: datetime
    ai_adjustment: int = 0  # bounded points from the AI importance check; 0 = none
    ai_label: str | None = None  # e.g. "major market impact"


@dataclass(frozen=True, slots=True)
class Reason:
    kind: str  # pin, entity, topic, event_type, keyword, source, region, importance
    label: str
    weight: int | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "weight": self.weight}


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int  # what the feed ranks by: rule score plus any AI adjustment
    relevance: int
    importance: int  # factor x 100
    pinned: bool
    excluded: bool
    reasons: tuple[Reason, ...]
    rule_score: int | None = None  # score from rules alone; alerts use this. None = score

    @property
    def alert_score(self) -> int:
        return self.score if self.rule_score is None else self.rule_score

    @property
    def tier(self) -> str | None:
        return tier_of(self.score)


@dataclass
class CompiledProfile:
    """A profile's rules, prepared for fast repeated matching."""

    id: uuid.UUID | None
    version: int
    rules: list[RuleSpec]
    show_threshold: int = 50
    alert_threshold: int = 80
    source_names: dict[str, str] = field(default_factory=dict)

    @cached_property
    def _keyword_res(self) -> dict[str, re.Pattern[str]]:
        return {
            r.value: re.compile(rf"(?<!\w){re.escape(r.value.lower())}(?!\w)")
            for r in self.rules
            if r.kind == "keyword"
        }

    def matches(self, rule: RuleSpec, story: StoryFacts) -> bool:
        match rule.kind:
            case "topic":
                return rule.value in story.topics
            case "entity":
                return rule.value in story.entities
            case "event_type":
                return story.event_type == rule.value and (
                    rule.entity is None or rule.entity in story.entities
                )
            case "keyword":
                return bool(self._keyword_res[rule.value].search(story.match_text))
            case "source":
                return rule.value in story.source_slugs
            case "region":
                return rule.value in story.regions
        return False

    def label(self, rule: RuleSpec) -> str:
        match rule.kind:
            case "topic":
                return f"Topic: {_name(TOPICS_BY_SLUG, rule.value)}"
            case "entity":
                return f"Entity: {_name(ENTITIES_BY_SLUG, rule.value)}"
            case "event_type":
                event = _name(EVENT_TYPES_BY_SLUG, rule.value)
                if rule.entity:
                    return f"{_name(ENTITIES_BY_SLUG, rule.entity)} {event[0].lower()}{event[1:]}"
                return f"Event: {event}"
            case "keyword":
                return f"Keyword: {rule.value}"
            case "source":
                return f"Source: {self.source_names.get(rule.value, rule.value)}"
            case "region":
                return f"Region: {rule.value}"
        return rule.value


def _name(table: dict, slug: str) -> str:
    item = table.get(slug)
    return item.name if item is not None else slug


def importance_factor(story: StoryFacts) -> tuple[float, list[Reason]]:
    authority = (min(max(story.max_authority, 1), 5) - 1) / 4
    event = EVENT_SIGNIFICANCE.get(story.event_type or "", DEFAULT_EVENT_SIGNIFICANCE)
    coverage = min(1.0, (story.source_count - 1) / 3)
    raw = W_AUTHORITY * authority + W_EVENT * event + W_COVERAGE * coverage

    notes = []
    if story.has_official_source:
        notes.append(Reason("importance", "Official source"))
    if story.source_count > 1:
        notes.append(Reason("importance", f"{story.source_count} sources"))
    if story.is_routine:
        notes.append(Reason("importance", "Routine release"))
    return 0.5 + 0.5 * raw, notes


def score_story(profile: CompiledProfile, story: StoryFacts, now: datetime) -> ScoreResult | None:
    """None when nothing in the profile matches (the story is irrelevant to it)."""
    active = [r for r in profile.rules if r.expires_at is None or r.expires_at > now]
    matched = [r for r in active if profile.matches(r, story)]
    if not matched:
        return None

    includes = [r for r in matched if r.mode == "include"]
    pins = [r for r in matched if r.mode == "pin"]
    excludes = [r for r in matched if r.mode == "exclude"]

    miss = 1.0
    for rule in includes:
        miss *= 1 - min(1.0, rule.weight / 10 * KIND_FACTOR[rule.kind])
    relevance = 100 * (1 - miss)
    if includes and all(r.kind in WEAK_KINDS for r in includes):
        relevance = min(relevance, WEAK_ONLY_RELEVANCE_CAP)

    factor, importance_notes = importance_factor(story)
    score = round(relevance * factor)
    pinned = bool(pins)
    if pinned:
        score = max(score, max(r.min_score or 0 for r in pins))
    excluded = bool(excludes) and not pinned
    rule_score = max(0, min(100, score))

    if story.ai_adjustment:
        score += story.ai_adjustment
        if pinned:  # a pin's guarantee holds whatever AI thinks
            score = max(score, max(r.min_score or 0 for r in pins))
    score = max(0, min(100, score))
    effect = score - rule_score  # what AI actually changed, after pins and bounds

    reasons: list[Reason] = [Reason("pin", f"Pinned: {profile.label(r)}") for r in pins]
    reasons += [Reason("exclude", f"Muted: {profile.label(r)}") for r in excludes]
    ranked = sorted(includes, key=lambda r: -r.weight * KIND_FACTOR[r.kind])
    reasons += [Reason(r.kind, profile.label(r), r.weight) for r in ranked]
    reasons += importance_notes
    if effect:
        reasons.append(Reason("ai", f"AI: {story.ai_label} ({effect:+d})"))

    return ScoreResult(
        score=score,
        rule_score=rule_score if effect else None,
        relevance=round(relevance),
        importance=round(factor * 100),
        pinned=pinned,
        excluded=excluded,
        reasons=tuple(reasons),
    )
