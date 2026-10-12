"""Decide which story an article belongs to.

Headlines about one event share few characters ("DMart Q2 Results: profit rises 9%" vs
"Avenue Supermarts Q2: DMart net profit jumps 8.5%"), so trigram similarity on the whole
title is a weak signal. We compare *significant words* instead (stop words removed, light
stemming, numbers kept) using the Dice coefficient, combined with tags:

  1. Same normalised headline within the window             -> same story
  2. Word similarity >= STRONG                                -> same story
  3. Word similarity >= MODERATE, >= 3 shared words, and a
     shared specific entity or the same event type            -> same story
  4. Same *specific* event type (rate decision, CPI print, results...) about the same
     anchor entity (RBI, a company...) within SPECIFIC_WINDOW -> same story

Otherwise a new story. The rules lean towards splitting: a duplicate card costs the reader
less than a story hidden inside another. Articles from the *same publisher* (all RBI feeds
count as one) are only merged on rule 1, because outlets publish series with near-identical
titles ("Money Market Operations as on 9 Oct", "... 10 Oct") that are different events.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.processing.taxonomy import ENTITIES_BY_SLUG, EVENT_TYPES_BY_SLUG

WINDOW = timedelta(hours=48)
SPECIFIC_WINDOW = timedelta(hours=24)
STRONG = 0.55
MODERATE = 0.40
MIN_SHARED_WORDS = 3

# Country-level entities are too broad to say two articles are about the same thing.
_BROAD_ENTITY_KINDS = {"country"}

_STOP_WORDS = frozenset(
    """
    a an the and or but nor of in on at to for from by with as into over under after before
    about amid against between during without within via per vs versus than then so if
    is are was were be been being am has have had do does did will would shall should can
    could may might must it its it's this that these those there here their they them he she
    his her we our you your i me my who whom whose which what when where why how all any
    each more most other some such no not only own same too very just also up down out off
    new says said say report reports according check details know heres here's live
    update updates today latest key big top why what how explained explainer amp
    """.split()
)
_TOKEN = re.compile(r"[₹$€£]?\d+(?:[.,]\d+)*%?|[a-z][a-z0-9&+'’-]*")


def _stem(word: str) -> str:
    """Tiny suffix stripper: enough to equate debut/debuts, scrapped/scrapping, rises/rise."""
    word = word.replace("’", "'").removesuffix("'s")
    for suffix, min_len in (("ies", 5), ("ing", 6), ("ed", 5), ("es", 5), ("s", 4)):
        if word.endswith(suffix) and len(word) >= min_len:
            stem = word[: -len(suffix)]
            return stem + "y" if suffix == "ies" else stem
    return word


def significant_words(title: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKC", title).casefold()
    words = set()
    for raw in _TOKEN.findall(folded):
        token = raw.strip("'’-")
        if not token or token in _STOP_WORDS:
            continue
        if token[0].isalpha() and len(token) < 2:
            continue
        words.add(token if not token[0].isalpha() else _stem(token))
    return frozenset(words)


def dice(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


@dataclass(frozen=True, slots=True)
class ArticleFeatures:
    article_id: uuid.UUID
    publisher: str  # host of the source homepage: rbi.org.in, livemint.com
    title_hash: bytes
    words: frozenset[str]
    entities: frozenset[str]
    event_type: str | None
    published_at: datetime
    story_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Match:
    story_id: uuid.UUID
    rule: str
    score: float


def _specific_entities(entities: frozenset[str]) -> frozenset[str]:
    return frozenset(
        e
        for e in entities
        if (ent := ENTITIES_BY_SLUG.get(e)) is not None and ent.kind not in _BROAD_ENTITY_KINDS
    )


# If both articles name an actor of one of these kinds and they share none, they are about
# different actors: "Fed cuts rates" vs "ECB cuts rates", "HDFC Bank Q2" vs "ICICI Bank Q2".
_EXCLUSIVE_KINDS = ("central_bank", "regulator", "company")


def conflicting_actors(a: frozenset[str], b: frozenset[str]) -> bool:
    for kind in _EXCLUSIVE_KINDS:
        a_kind = {e for e in a if (ent := ENTITIES_BY_SLUG.get(e)) and ent.kind == kind}
        b_kind = {e for e in b if (ent := ENTITIES_BY_SLUG.get(e)) and ent.kind == kind}
        if a_kind and b_kind and not (a_kind & b_kind):
            return True
    return False


def _anchor_overlap(event_type: str, a: frozenset[str], b: frozenset[str]) -> bool:
    kinds = EVENT_TYPES_BY_SLUG[event_type].anchor_kinds
    return any((ent := ENTITIES_BY_SLUG.get(e)) is not None and ent.kind in kinds for e in a & b)


def match_pair(article: ArticleFeatures, other: ArticleFeatures) -> Match | None:
    """Would `article` join `other`'s story? `other` must already have a story."""
    assert other.story_id is not None
    gap = abs(article.published_at - other.published_at)
    if gap > WINDOW:
        return None

    if article.title_hash == other.title_hash:
        return Match(other.story_id, "same_title", 1.0)
    if article.publisher == other.publisher:
        return None
    if conflicting_actors(article.entities, other.entities):
        return None

    similarity = dice(article.words, other.words)
    if similarity >= STRONG:
        return Match(other.story_id, "similar_title", similarity)

    if similarity >= MODERATE and len(article.words & other.words) >= MIN_SHARED_WORDS:
        shared_specific = _specific_entities(article.entities) & _specific_entities(other.entities)
        same_event = article.event_type is not None and article.event_type == other.event_type
        if shared_specific or same_event:
            return Match(other.story_id, "similar_title_and_tags", similarity)

    event = article.event_type
    if (
        event is not None
        and event == other.event_type
        and EVENT_TYPES_BY_SLUG[event].specific
        and gap <= SPECIFIC_WINDOW
        and _anchor_overlap(event, article.entities, other.entities)
    ):
        return Match(other.story_id, "same_specific_event", 0.5 + similarity / 2)

    return None


_RULE_RANK = {"same_title": 3, "similar_title": 2, "same_specific_event": 1}


@dataclass
class ClusterIndex:
    """In-memory view of recent clustered articles, used while processing one batch.

    Brute force over the window is fine at our volume (a few thousand articles per 48h);
    each comparison is a couple of set operations.
    """

    articles: list[ArticleFeatures] = field(default_factory=list)

    def add(self, features: ArticleFeatures) -> None:
        self.articles.append(features)

    def find(self, article: ArticleFeatures) -> Match | None:
        best: Match | None = None
        for other in self.articles:
            match = match_pair(article, other)
            if match is None:
                continue
            key = (_RULE_RANK.get(match.rule, 1), match.score)
            if best is None or key > (_RULE_RANK.get(best.rule, 1), best.score):
                best = match
        return best
