"""Profiles: create from a template, replace rules, ownership checks, validation."""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Profile, ProfileRule, RuleKind, RuleMode, Source
from app.processing.taxonomy import (
    ENTITIES,
    ENTITIES_BY_SLUG,
    EVENT_TYPES_BY_SLUG,
    TOPICS_BY_SLUG,
)
from app.scoring.engine import CompiledProfile, RuleSpec
from app.scoring.templates import TEMPLATES

MAX_RULES_PER_PROFILE = 200
KEYWORD_MIN, KEYWORD_MAX = 2, 60
REGIONS = frozenset({e.region for e in ENTITIES if e.region} | {"GLOBAL"})


class ProfileError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RuleInput:
    kind: str
    value: str
    mode: str = RuleMode.INCLUDE
    weight: int = 5
    entity: str | None = None
    min_score: int | None = None
    expires_at: datetime | None = None


@dataclass
class ProfileInput:
    name: str
    show_threshold: int
    alert_threshold: int
    rules: list[RuleInput] = field(default_factory=list)


def _invalid(message: str) -> ProfileError:
    return ProfileError(422, "invalid_rule", message)


def validate_rules(rules: list[RuleInput], source_slugs: set[str]) -> list[RuleInput]:
    """Check every rule against the taxonomy and drop exact duplicates."""
    if len(rules) > MAX_RULES_PER_PROFILE:
        raise _invalid(f"A profile can have at most {MAX_RULES_PER_PROFILE} rules.")
    known = {
        RuleKind.TOPIC: TOPICS_BY_SLUG.keys(),
        RuleKind.ENTITY: ENTITIES_BY_SLUG.keys(),
        RuleKind.EVENT_TYPE: EVENT_TYPES_BY_SLUG.keys(),
        RuleKind.REGION: REGIONS,
        RuleKind.SOURCE: source_slugs,
    }
    seen: set[tuple] = set()
    clean: list[RuleInput] = []
    for rule in rules:
        value = rule.value.strip()
        if rule.kind == RuleKind.KEYWORD:
            if not KEYWORD_MIN <= len(value) <= KEYWORD_MAX:
                raise _invalid(f"Keywords must be {KEYWORD_MIN}-{KEYWORD_MAX} characters.")
        elif rule.kind in known:
            if value not in known[rule.kind]:
                raise _invalid(f"Unknown {rule.kind}: {value!r}.")
        else:
            raise _invalid(f"Unknown rule kind: {rule.kind!r}.")

        if rule.entity is not None:
            if rule.kind != RuleKind.EVENT_TYPE:
                raise _invalid("Only event-type rules can be narrowed to an entity.")
            if rule.entity not in ENTITIES_BY_SLUG:
                raise _invalid(f"Unknown entity: {rule.entity!r}.")
        if rule.mode == RuleMode.PIN and rule.min_score is None:
            raise _invalid("Pinned rules need a minimum score.")
        if rule.mode != RuleMode.PIN and rule.min_score is not None:
            raise _invalid("Only pinned rules have a minimum score.")

        key = (rule.kind, value.lower(), rule.entity, rule.mode)
        if key in seen:
            continue
        seen.add(key)
        clean.append(
            RuleInput(rule.kind, value, rule.mode, rule.weight, rule.entity, rule.min_score,
                      rule.expires_at)
        )  # fmt: skip
    return clean


def validate_thresholds(show: int, alert: int) -> None:
    if not (20 <= show <= 100 and 20 <= alert <= 100):
        raise ProfileError(422, "invalid_threshold", "Thresholds must be between 20 and 100.")
    if alert < show:
        raise ProfileError(
            422, "invalid_threshold", "The alert threshold can't be below the show threshold."
        )


async def source_slugs(db: AsyncSession) -> dict[str, str]:
    """slug -> display name for every known source."""
    return {slug: name for slug, name in await db.execute(select(Source.slug, Source.name))}


def template_input(template_slug: str, name: str | None = None) -> ProfileInput:
    template = TEMPLATES.get(template_slug)
    if template is None:
        raise ProfileError(422, "unknown_template", f"Unknown template: {template_slug!r}.")
    return ProfileInput(
        name=name or template.name,
        show_threshold=template.show_threshold,
        alert_threshold=template.alert_threshold,
        rules=[
            RuleInput(r.kind, r.value, r.mode, r.weight, r.entity, r.min_score)
            for r in template.rules
        ],
    )


def _rule_rows(profile_id: uuid.UUID, rules: list[RuleInput]) -> list[ProfileRule]:
    return [
        ProfileRule(
            profile_id=profile_id,
            kind=r.kind,
            value=r.value,
            entity=r.entity,
            mode=r.mode,
            weight=r.weight,
            base_weight=r.weight,
            min_score=r.min_score,
            expires_at=r.expires_at,
        )
        for r in rules
    ]


async def create_profile(
    db: AsyncSession, user_id: uuid.UUID, data: ProfileInput, *, template: str | None = None
) -> Profile:
    count = await db.scalar(
        select(func.count()).select_from(Profile).where(Profile.user_id == user_id)
    )
    from app.billing.plans import PlanLimitError, entitlements

    plan = await entitlements(db, user_id)
    limit = min(plan.max_profiles, get_settings().max_profiles_per_user)
    if count >= limit:
        if limit < get_settings().max_profiles_per_user:
            raise PlanLimitError(
                "profile_limit",
                f"Your {plan.plan_name} plan includes {limit} "
                f"profile{'s' if limit != 1 else ''}. Upgrade for more.",
                needs="pro" if plan.plan == "free" else "pro_plus",
            )
        raise ProfileError(409, "profile_limit", f"You can have at most {limit} profiles.")
    validate_thresholds(data.show_threshold, data.alert_threshold)
    rules = validate_rules(data.rules, set((await source_slugs(db)).keys()))

    profile = Profile(
        user_id=user_id,
        name=data.name.strip(),
        template=template,
        show_threshold=data.show_threshold,
        alert_threshold=data.alert_threshold,
    )
    db.add(profile)
    await db.flush()
    db.add_all(_rule_rows(profile.id, rules))
    await db.flush()
    return profile


async def replace_profile(db: AsyncSession, profile: Profile, data: ProfileInput) -> Profile:
    validate_thresholds(data.show_threshold, data.alert_threshold)
    rules = validate_rules(data.rules, set((await source_slugs(db)).keys()))
    profile.name = data.name.strip()
    profile.show_threshold = data.show_threshold
    profile.alert_threshold = data.alert_threshold
    profile.version += 1
    await db.execute(delete(ProfileRule).where(ProfileRule.profile_id == profile.id))
    db.add_all(_rule_rows(profile.id, rules))
    await db.flush()
    return profile


async def get_owned_profile(
    db: AsyncSession, user_id: uuid.UUID, profile_id: uuid.UUID, *, for_update: bool = False
) -> Profile:
    stmt = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    profile = await db.scalar(stmt)
    if profile is None:  # someone else's profile looks exactly like a missing one
        raise ProfileError(404, "profile_not_found", "Profile not found.")
    return profile


async def default_profile(db: AsyncSession, user_id: uuid.UUID) -> Profile | None:
    return await db.scalar(
        select(Profile).where(Profile.user_id == user_id).order_by(Profile.created_at).limit(1)
    )


async def load_rules(db: AsyncSession, profile_ids: list[uuid.UUID]) -> dict[uuid.UUID, list]:
    by_profile: dict[uuid.UUID, list[ProfileRule]] = defaultdict(list)
    rows = await db.scalars(
        select(ProfileRule)
        .where(ProfileRule.profile_id.in_(profile_ids))
        .order_by(ProfileRule.mode, ProfileRule.weight.desc(), ProfileRule.kind)
    )
    for rule in rows:
        by_profile[rule.profile_id].append(rule)
    return by_profile


def to_spec(rule: ProfileRule | RuleInput) -> RuleSpec:
    return RuleSpec(
        kind=rule.kind,
        value=rule.value,
        mode=rule.mode,
        weight=rule.weight,
        entity=rule.entity,
        min_score=rule.min_score,
        expires_at=rule.expires_at,
        id=getattr(rule, "id", None),
    )


def compile_profile(
    profile: Profile, rules: list[ProfileRule], source_names: dict[str, str]
) -> CompiledProfile:
    return CompiledProfile(
        id=profile.id,
        version=profile.version,
        rules=[to_spec(r) for r in rules],
        show_threshold=profile.show_threshold,
        alert_threshold=profile.alert_threshold,
        source_names=source_names,
    )
