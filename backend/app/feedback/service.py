"""'Relevant' / 'not relevant' feedback that tunes a profile, visibly and reversibly.

Each verdict nudges the weights of the include rules that matched the story by one step
(down for 'not relevant', up for 'relevant'). Learning never moves a weight more than
`feedback_max_drift` from what the user set (`base_weight`), and reset_learning() undoes it.
After repeated 'not relevant' on stories about the same company or institution, we suggest
muting it (the user decides).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Profile, ProfileRule, RuleKind, RuleMode, Story, StoryFeedback, Verdict
from app.processing.taxonomy import ENTITIES_BY_SLUG
from app.profiles.service import ProfileError, compile_profile, load_rules, source_slugs
from app.scoring.service import facts, rescore_profile

SUGGESTION_WINDOW = timedelta(days=30)
_MUTABLE_KINDS = {"company", "central_bank", "regulator", "institution", "commodity", "index"}


@dataclass
class Adjustment:
    rule_id: uuid.UUID
    label: str
    before: int
    after: int


@dataclass
class FeedbackResult:
    verdict: str
    adjustments: list[Adjustment] = field(default_factory=list)
    suggestion: dict | None = None


def _bounds(rule: ProfileRule) -> tuple[int, int]:
    drift = get_settings().feedback_max_drift
    base = rule.base_weight or rule.weight
    return max(1, base - drift), min(10, base + drift)


async def record_feedback(
    db: AsyncSession, user_id: uuid.UUID, profile: Profile, story_id: uuid.UUID, verdict: Verdict
) -> FeedbackResult:
    story = await db.get(Story, story_id)
    if story is None:
        raise ProfileError(404, "story_not_found", "Story not found.")

    await db.execute(
        pg_insert(StoryFeedback)
        .values(user_id=user_id, profile_id=profile.id, story_id=story_id, verdict=verdict)
        .on_conflict_do_update(
            index_elements=[StoryFeedback.profile_id, StoryFeedback.story_id],
            set_={"verdict": verdict, "created_at": func.now()},
        )
    )

    rules = (await load_rules(db, [profile.id])).get(profile.id, [])
    compiled = compile_profile(profile, rules, await source_slugs(db))
    story_facts = facts(story)
    step = get_settings().feedback_step * (1 if verdict == Verdict.RELEVANT else -1)

    result = FeedbackResult(verdict=verdict)
    now = datetime.now(UTC)
    for rule, spec in zip(rules, compiled.rules, strict=True):
        if rule.mode != RuleMode.INCLUDE or rule.kind == RuleKind.REGION:
            continue
        if spec.expires_at is not None and spec.expires_at <= now:
            continue
        if not compiled.matches(spec, story_facts):
            continue
        low, high = _bounds(rule)
        new_weight = min(high, max(low, rule.weight + step))
        if new_weight != rule.weight:
            result.adjustments.append(
                Adjustment(rule.id, compiled.label(spec), rule.weight, new_weight)
            )
            rule.weight = new_weight

    if result.adjustments:
        profile.version += 1
        await db.flush()
        await rescore_profile(db, profile)

    if verdict == Verdict.NOT_RELEVANT:
        result.suggestion = await _mute_suggestion(db, profile, story, rules)
    return result


async def _mute_suggestion(
    db: AsyncSession, profile: Profile, story: Story, rules: list[ProfileRule]
) -> dict | None:
    muted = {r.value for r in rules if r.mode == RuleMode.EXCLUDE and r.kind == RuleKind.ENTITY}
    candidates = [
        e
        for e in story.entities
        if e not in muted
        and (ent := ENTITIES_BY_SLUG.get(e)) is not None
        and ent.kind in _MUTABLE_KINDS
    ]
    threshold = get_settings().feedback_suggest_mute_after
    since = datetime.now(UTC) - SUGGESTION_WINDOW
    best: tuple[int, str] | None = None
    for entity in candidates:
        count = await db.scalar(
            select(func.count())
            .select_from(StoryFeedback)
            .join(Story, Story.id == StoryFeedback.story_id)
            .where(
                StoryFeedback.profile_id == profile.id,
                StoryFeedback.verdict == Verdict.NOT_RELEVANT,
                StoryFeedback.created_at >= since,
                Story.entities.contains([entity]),
            )
        )
        if count >= threshold and (best is None or count > best[0]):
            best = (count, entity)
    if best is None:
        return None
    count, entity = best
    return {
        "kind": "entity",
        "value": entity,
        "label": ENTITIES_BY_SLUG[entity].name,
        "count": count,
    }


async def reset_learning(db: AsyncSession, profile: Profile) -> int:
    """Put every learned weight back to what the user set. Returns how many changed."""
    rules = (await load_rules(db, [profile.id])).get(profile.id, [])
    changed = 0
    for rule in rules:
        if rule.base_weight is not None and rule.weight != rule.base_weight:
            rule.weight = rule.base_weight
            changed += 1
    if changed:
        profile.version += 1
        await db.flush()
        await rescore_profile(db, profile)
    return changed
