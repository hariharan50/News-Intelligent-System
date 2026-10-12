"""Rule-based tagging: topics, entities, event type and regions for one article."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from app.processing.taxonomy import (
    ENTITIES,
    EVENT_TYPES,
    SOURCE_ENTITIES,
    TOPICS,
    Entity,
    EventType,
    Topic,
)

# Event types imply a topic even if no topic keyword appears.
EVENT_TOPICS: dict[str, str] = {
    "rate_decision": "monetary_policy",
    "rate_outlook": "monetary_policy",
    "inflation_data": "inflation",
    "gdp_data": "economy",
    "economic_data": "economy",
    "currency_intervention": "currency",
    "regulatory_change": "market_regulation",
    "regulatory_order": "market_regulation",
    "earnings_result": "earnings",
    "military_conflict": "geopolitics",
    "sanctions": "geopolitics",
    "tariff_action": "trade",
    "supply_disruption": "energy",
}


@dataclass(frozen=True, slots=True)
class Tags:
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    event_type: str | None
    regions: tuple[str, ...]


def _phrase_regex(phrases: Iterable[str], *, ignore_case: bool) -> re.Pattern[str] | None:
    """Whole-phrase alternation; longest first so "Bank Nifty" wins over "Nifty".

    Uses (?<!\\w) / (?!\\w) instead of \\b so phrases that start or end with punctuation
    ("L&T", "OPEC+", "U.S.") still match as whole words.
    """
    phrases = sorted({p for p in phrases if p}, key=len, reverse=True)
    if not phrases:
        return None
    body = "|".join(re.escape(p) for p in phrases)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE if ignore_case else 0)


def _any_regex(patterns: Iterable[str]) -> re.Pattern[str] | None:
    patterns = list(patterns)
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


class Tagger:
    def __init__(
        self,
        topics: Iterable[Topic] = TOPICS,
        entities: Iterable[Entity] = ENTITIES,
        event_types: Iterable[EventType] = EVENT_TYPES,
        source_entities: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._entities = {e.slug: e for e in entities}
        self._source_entities = SOURCE_ENTITIES if source_entities is None else source_entities
        self._topic_res = [(t.slug, _any_regex(t.patterns)) for t in topics]
        self._entity_res = [
            (
                e.slug,
                _phrase_regex(e.aliases, ignore_case=True),
                _phrase_regex(e.acronyms, ignore_case=False),
            )
            for e in self._entities.values()
        ]
        self._event_res = [
            (ev.slug, _any_regex(ev.patterns), _any_regex(ev.exclude)) for ev in event_types
        ]

    def entities_in(self, text: str) -> list[str]:
        found = []
        for slug, aliases, acronyms in self._entity_res:
            if (aliases and aliases.search(text)) or (acronyms and acronyms.search(text)):
                found.append(slug)
        return found

    def event_type_of(self, text: str) -> str | None:
        for slug, include, exclude in self._event_res:
            if include and include.search(text) and not (exclude and exclude.search(text)):
                return slug
        return None

    def tag(
        self,
        *,
        title: str,
        excerpt: str | None = None,
        feed_tags: Iterable[str] = (),
        source_slug: str | None = None,
        source_region: str | None = None,
    ) -> Tags:
        body = "\n".join(filter(None, [title, excerpt, " ; ".join(feed_tags)]))

        entity_slugs = set(self.entities_in(body))
        entity_slugs.update(self._source_entities.get(source_slug or "", ()))

        # The event is what the headline says happened. Excerpts mention background events
        # ("...amid the war", "...ahead of the Fed") and make event typing noisy.
        event_type = self.event_type_of(title)

        topic_slugs = {slug for slug, rx in self._topic_res if rx and rx.search(body)}
        for slug in entity_slugs:
            topic_slugs.update(self._entities[slug].topics)
        if event_type in EVENT_TOPICS:
            topic_slugs.add(EVENT_TOPICS[event_type])

        regions = {self._entities[s].region for s in entity_slugs if self._entities[s].region}
        if source_region and source_region != "GLOBAL":
            regions.add(source_region)
        if not regions:
            regions.add("GLOBAL")

        return Tags(
            topics=tuple(sorted(topic_slugs)),
            entities=tuple(sorted(entity_slugs)),
            event_type=event_type,
            regions=tuple(sorted(regions)),
        )


@lru_cache
def get_tagger() -> Tagger:
    """Compiling ~500 patterns takes a moment; do it once per process."""
    return Tagger()
