"""Write and refresh stored scores.

Scores are computed when something changes and stored, so reading a feed never scores:
  * score_stories(): stories were created or updated by processing -> score them for every
    profile that could care (found through the rule index, not by scanning all users).
  * rescore_profile(): a profile was created or edited -> score its recent stories.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.service import create_alerts
from app.billing.plans import entitlements_for
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.events import publish_to_user
from app.models import Profile, ProfileRule, RuleKind, RuleMode, Story, StoryScore
from app.profiles.service import compile_profile, load_rules, source_slugs
from app.scoring.engine import CompiledProfile, ScoreResult, StoryFacts, score_story, tier_of

log = logging.getLogger(__name__)


# A story already in a feed is re-announced only if its score rose at least this much.
ANNOUNCE_MIN_RISE = 5
ANNOUNCE_MAX_AGE = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ScoredPair:
    profile_id: uuid.UUID
    story_id: uuid.UUID
    score: int
    user_id: uuid.UUID
    show_threshold: int
    alert_threshold: int
    previous_score: int | None = None  # this profile's score before this run, if any
    title: str = ""
    pinned: bool = False
    reasons: tuple[dict, ...] = ()
    story_published_at: datetime | None = None
    # Rule-only scores (before any AI adjustment). Alerts compare these, never `score`.
    rule_score: int | None = None
    previous_rule_score: int | None = None

    @property
    def alert_score(self) -> int:
        return self.score if self.rule_score is None else self.rule_score

    @property
    def is_fresh(self) -> bool:
        """Only fresh news is announced or alerted: a new source's first poll brings old items."""
        return (
            self.story_published_at is not None
            and self.story_published_at >= datetime.now(UTC) - ANNOUNCE_MAX_AGE
        )

    @property
    def is_new_in_feed(self) -> bool:
        before = self.previous_score
        return self.score >= self.show_threshold and (
            before is None or before < self.show_threshold
        )

    @property
    def crossed_alert(self) -> bool:
        """Rule score crossed the alert line. AI adjustments can't trigger an alert."""
        before = self.previous_rule_score
        return self.alert_score >= self.alert_threshold and (
            before is None or before < self.alert_threshold
        )

    @property
    def worth_announcing(self) -> bool:
        """New to the feed, newly alert-worthy, or clearly more important than before.
        Re-scoring an unchanged story (re-processing, safety-net runs) stays silent."""
        if self.score < self.show_threshold:
            return False
        rose = (
            self.previous_score is not None
            and self.score - self.previous_score >= ANNOUNCE_MIN_RISE
        )
        return self.is_new_in_feed or self.crossed_alert or rose

    def event(self) -> dict:
        return {
            "type": "story",
            "profile_id": str(self.profile_id),
            "story_id": str(self.story_id),
            "title": self.title,
            "score": self.score,
            "tier": tier_of(self.score),
            "alert": self.alert_score >= self.alert_threshold,
            "new": self.is_new_in_feed,
        }


# Claude's 1-5 market-impact rating -> share of the maximum adjustment, and its label.
AI_IMPACT = {
    1: (-1.0, "minor market impact"),
    2: (-0.5, "limited market impact"),
    3: (0.0, "moderate market impact"),
    4: (0.5, "major market impact"),
    5: (1.0, "very major market impact"),
}


def ai_adjustment(story: Story) -> tuple[int, str | None]:
    s = get_settings()
    if story.ai_impact not in AI_IMPACT or not (s.feature_ai and s.ai_importance_enabled):
        return 0, None
    share, label = AI_IMPACT[story.ai_impact]
    return round(share * s.ai_max_adjustment), label


def facts(story: Story) -> StoryFacts:
    adjustment, label = ai_adjustment(story)
    return StoryFacts(
        id=story.id,
        topics=frozenset(story.topics),
        entities=frozenset(story.entities),
        event_type=story.event_type,
        regions=frozenset(story.regions),
        source_slugs=frozenset(story.source_slugs),
        match_text=story.match_text,
        max_authority=story.max_authority,
        source_count=story.source_count,
        is_routine=story.is_routine,
        has_official_source=story.has_official_source,
        published_at=story.first_published_at,
        ai_adjustment=adjustment,
        ai_label=label,
    )


def _row(profile: CompiledProfile, story: Story, result: ScoreResult, now: datetime) -> dict:
    return {
        "profile_id": profile.id,
        "story_id": story.id,
        "score": result.score,
        "rule_score": result.rule_score,
        "relevance": result.relevance,
        "importance": result.importance,
        "pinned": result.pinned,
        "reasons": [r.as_dict() for r in result.reasons],
        "story_published_at": story.first_published_at,
        "profile_version": profile.version,
        "scored_at": now,
    }


def _keep(result: ScoreResult | None) -> bool:
    return result is not None and not result.excluded and result.score >= get_settings().score_floor


async def _insert(db: AsyncSession, rows: list[dict]) -> None:
    # Chunked: PostgreSQL allows at most 32767 bind parameters per statement.
    for i in range(0, len(rows), 2000):
        chunk = rows[i : i + 2000]
        stmt = pg_insert(StoryScore).values(chunk)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[StoryScore.profile_id, StoryScore.story_id],
                set_={c: stmt.excluded[c] for c in chunk[0] if c not in ("profile_id", "story_id")},
            )
        )


async def _compiled(db: AsyncSession, profiles: list[Profile]) -> list[CompiledProfile]:
    rules = await load_rules(db, [p.id for p in profiles])
    names = await source_slugs(db)
    return [compile_profile(p, rules.get(p.id, []), names) for p in profiles]


async def _candidate_profiles(db: AsyncSession, stories: list[Story]) -> list[Profile]:
    topics = {t for s in stories for t in s.topics}
    entities = {e for s in stories for e in s.entities}
    events = {s.event_type for s in stories if s.event_type}
    sources = {x for s in stories for x in s.source_slugs}
    regions = {g for s in stories for g in s.regions}

    def kind_in(kind: RuleKind, values: set[str]):
        return and_(ProfileRule.kind == kind, ProfileRule.value.in_(values))

    matches_something = or_(
        kind_in(RuleKind.TOPIC, topics),
        kind_in(RuleKind.ENTITY, entities),
        kind_in(RuleKind.EVENT_TYPE, events),
        kind_in(RuleKind.SOURCE, sources),
        kind_in(RuleKind.REGION, regions),
        ProfileRule.kind == RuleKind.KEYWORD,  # text match: can't be pre-filtered by index
    )
    candidate_ids = (
        select(ProfileRule.profile_id)
        .where(ProfileRule.mode.in_([RuleMode.INCLUDE, RuleMode.PIN]), matches_something)
        .distinct()
    )
    return list(await db.scalars(select(Profile).where(Profile.id.in_(candidate_ids))))


async def score_stories(
    session_factory: async_sessionmaker[AsyncSession],
    story_ids: list[uuid.UUID],
    *,
    announce: bool = True,
) -> list[ScoredPair]:
    """(Re)score these stories for every profile that could care. Returns stored scores.

    announce=False for maintenance (reprocess/rescore): scores change, nobody is notified.
    """
    if not story_ids:
        return []
    now = datetime.now(UTC)
    async with session_factory() as db, db.begin():
        stories = list(await db.scalars(select(Story).where(Story.id.in_(story_ids))))
        if not stories:
            return []
        candidates = await _candidate_profiles(db, stories)
        owners = {p.id: p.user_id for p in candidates}
        profiles = await _compiled(db, candidates)
        previous = {
            (pid, sid): (score, rule_score)
            for pid, sid, score, rule_score in await db.execute(
                select(
                    StoryScore.profile_id,
                    StoryScore.story_id,
                    StoryScore.score,
                    func.coalesce(StoryScore.rule_score, StoryScore.score),
                ).where(StoryScore.story_id.in_(story_ids))
            )
        }

        rows, stored = [], []
        for story in stories:
            story_facts = facts(story)
            for profile in profiles:
                result = score_story(profile, story_facts, now)
                if _keep(result):
                    rows.append(_row(profile, story, result, now))
                    before, before_rule = previous.get((profile.id, story.id), (None, None))
                    stored.append(
                        ScoredPair(
                            profile_id=profile.id,
                            story_id=story.id,
                            score=result.score,
                            user_id=owners[profile.id],
                            show_threshold=profile.show_threshold,
                            alert_threshold=profile.alert_threshold,
                            previous_score=before,
                            rule_score=result.rule_score,
                            previous_rule_score=before_rule,
                            title=story.title,
                            pinned=result.pinned,
                            reasons=tuple(r.as_dict() for r in result.reasons),
                            story_published_at=story.first_published_at,
                        )
                    )

        # Replace, so a story that stopped matching a profile disappears from its feed.
        await db.execute(delete(StoryScore).where(StoryScore.story_id.in_(story_ids)))
        if rows:
            await _insert(db, rows)
        await db.execute(update(Story).where(Story.id.in_(story_ids)).values(scored_at=now))

    # After commit, so a browser that reacts to the event can already read the new score.
    if announce:
        to_announce = [p for p in stored if p.is_fresh and p.worth_announcing]
        if to_announce:
            async with session_factory() as db:
                plans = await entitlements_for(db, list({p.user_id for p in to_announce}))
            for pair in to_announce:
                # A delayed (free) feed shows the story later; don't announce it now.
                # Alerts still go out in real time.
                if plans[pair.user_id].feed_delay_minutes and not pair.crossed_alert:
                    continue
                await publish_to_user(redis_client, pair.user_id, pair.event())
        await create_alerts(session_factory, [p for p in stored if p.is_fresh and p.crossed_alert])

    log.info("scored %d story(ies) for %d profile(s): %d stored", len(stories), len(profiles),
             len(rows))  # fmt: skip
    return stored


async def score_unscored(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 500, announce: bool = True
) -> int:
    """Safety net: score stories changed since they were last scored (e.g. after a crash
    between processing and scoring)."""
    async with session_factory() as db:
        ids = list(
            await db.scalars(
                select(Story.id)
                .where(or_(Story.scored_at.is_(None), Story.scored_at < Story.updated_at))
                .order_by(Story.updated_at)
                .limit(limit)
            )
        )
    if ids:
        await score_stories(session_factory, ids, announce=announce)
    return len(ids)


async def recent_stories(db: AsyncSession, hours: int) -> list[Story]:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    return list(await db.scalars(select(Story).where(Story.last_published_at >= cutoff)))


async def rescore_profile(db: AsyncSession, profile: Profile) -> int:
    """Re-score a profile's recent stories inside the caller's transaction."""
    now = datetime.now(UTC)
    [compiled] = await _compiled(db, [profile])
    stories = await recent_stories(db, get_settings().rescore_window_hours)
    rows = []
    for story in stories:
        result = score_story(compiled, facts(story), now)
        if _keep(result):
            rows.append(_row(compiled, story, result, now))
    await db.execute(delete(StoryScore).where(StoryScore.profile_id == profile.id))
    if rows:
        await _insert(db, rows)
    return len(rows)


async def preview(
    db: AsyncSession, compiled: CompiledProfile, hours: int
) -> list[tuple[Story, ScoreResult]]:
    """Score recent stories against unsaved rules. Nothing is written."""
    now = datetime.now(UTC)
    results = []
    for story in await recent_stories(db, hours):
        result = score_story(compiled, facts(story), now)
        if _keep(result):
            results.append((story, result))
    results.sort(key=lambda sr: (-sr[1].score, -sr[0].first_published_at.timestamp()))
    return results
