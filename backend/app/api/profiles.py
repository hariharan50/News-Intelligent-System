import uuid
from collections import Counter
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth.deps import CurrentUserDep
from app.db.session import SessionDep
from app.models import Profile, ProfileRule, RuleKind, RuleMode
from app.profiles import service as profiles
from app.profiles.service import ProfileError, ProfileInput, RuleInput
from app.scoring.engine import CompiledProfile
from app.scoring.service import preview, rescore_profile

router = APIRouter(prefix="/profiles", tags=["profiles"])


class RuleIn(BaseModel):
    kind: RuleKind
    value: str = Field(min_length=1, max_length=80)
    mode: RuleMode = RuleMode.INCLUDE
    weight: int = Field(default=5, ge=1, le=10)
    entity: str | None = Field(default=None, max_length=60)
    min_score: int | None = Field(default=None, ge=20, le=100)
    expires_at: datetime | None = None

    def to_input(self) -> RuleInput:
        return RuleInput(
            self.kind, self.value, self.mode, self.weight, self.entity, self.min_score,
            self.expires_at,
        )  # fmt: skip


class RuleOut(RuleIn):
    id: uuid.UUID
    base_weight: int | None


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    show_threshold: int = Field(default=50, ge=20, le=100)
    alert_threshold: int = Field(default=80, ge=20, le=100)
    rules: list[RuleIn] = Field(default_factory=list, max_length=profiles.MAX_RULES_PER_PROFILE)

    def to_input(self) -> ProfileInput:
        return ProfileInput(
            self.name, self.show_threshold, self.alert_threshold, [r.to_input() for r in self.rules]
        )


class ProfileCreateIn(BaseModel):
    """Start from a template (rules copied, editable later) or from your own rules."""

    template: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    show_threshold: int | None = Field(default=None, ge=20, le=100)
    alert_threshold: int | None = Field(default=None, ge=20, le=100)
    rules: list[RuleIn] | None = Field(default=None, max_length=profiles.MAX_RULES_PER_PROFILE)


class ProfileSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    template: str | None
    show_threshold: int
    alert_threshold: int
    rule_count: int
    updated_at: datetime


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    template: str | None
    show_threshold: int
    alert_threshold: int
    version: int
    created_at: datetime
    updated_at: datetime
    rules: list[RuleOut]


class PreviewIn(BaseModel):
    show_threshold: int = Field(default=50, ge=20, le=100)
    alert_threshold: int = Field(default=80, ge=20, le=100)
    rules: list[RuleIn] = Field(max_length=profiles.MAX_RULES_PER_PROFILE)
    hours: int = Field(default=24, ge=1, le=168)


class PreviewStoryOut(BaseModel):
    story_id: uuid.UUID
    title: str
    url: str
    published_at: datetime
    score: int
    tier: str | None
    pinned: bool
    reasons: list[dict]


class PreviewOut(BaseModel):
    hours: int
    shown: int
    alerts: int
    by_tier: dict[str, int]
    stories: list[PreviewStoryOut]


async def _profile_out(db: SessionDep, profile: Profile) -> ProfileOut:
    rules = (await profiles.load_rules(db, [profile.id])).get(profile.id, [])
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        template=profile.template,
        show_threshold=profile.show_threshold,
        alert_threshold=profile.alert_threshold,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        rules=[
            RuleOut(
                id=r.id, kind=r.kind, value=r.value, mode=r.mode, weight=r.weight,
                entity=r.entity, min_score=r.min_score, expires_at=r.expires_at,
                base_weight=r.base_weight,
            )
            for r in rules
        ],
    )  # fmt: skip


@router.get("", response_model=list[ProfileSummaryOut])
async def list_profiles(current: CurrentUserDep, db: SessionDep) -> list[ProfileSummaryOut]:
    rows = await db.execute(
        select(Profile, func.count(ProfileRule.id))
        .outerjoin(ProfileRule, ProfileRule.profile_id == Profile.id)
        .where(Profile.user_id == current.user_id)
        .group_by(Profile.id)
        .order_by(Profile.created_at)
    )
    return [
        ProfileSummaryOut(
            id=p.id, name=p.name, template=p.template, show_threshold=p.show_threshold,
            alert_threshold=p.alert_threshold, rule_count=n, updated_at=p.updated_at,
        )
        for p, n in rows
    ]  # fmt: skip


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create(body: ProfileCreateIn, current: CurrentUserDep, db: SessionDep) -> ProfileOut:
    if body.template:
        data = profiles.template_input(body.template, body.name)
    else:
        if body.name is None:
            raise ProfileError(422, "name_required", "Give the profile a name.")
        data = ProfileInput(body.name, 50, 80, [])
    if body.show_threshold is not None:
        data.show_threshold = body.show_threshold
    if body.alert_threshold is not None:
        data.alert_threshold = body.alert_threshold
    if body.rules is not None:
        data.rules = [r.to_input() for r in body.rules]

    profile = await profiles.create_profile(db, current.user_id, data, template=body.template)
    await rescore_profile(db, profile)
    await db.commit()
    await db.refresh(profile)
    return await _profile_out(db, profile)


@router.get("/{profile_id}", response_model=ProfileOut)
async def get(profile_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> ProfileOut:
    return await _profile_out(db, await profiles.get_owned_profile(db, current.user_id, profile_id))


@router.put("/{profile_id}", response_model=ProfileOut)
async def replace(
    profile_id: uuid.UUID, body: ProfileIn, current: CurrentUserDep, db: SessionDep
) -> ProfileOut:
    """Replace the profile's settings and rules; its feed is re-scored before returning."""
    profile = await profiles.get_owned_profile(db, current.user_id, profile_id, for_update=True)
    await profiles.replace_profile(db, profile, body.to_input())
    await rescore_profile(db, profile)
    await db.commit()
    await db.refresh(profile)
    return await _profile_out(db, profile)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(profile_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    profile = await profiles.get_owned_profile(db, current.user_id, profile_id, for_update=True)
    count = await db.scalar(
        select(func.count()).select_from(Profile).where(Profile.user_id == current.user_id)
    )
    if count <= 1:
        raise ProfileError(409, "last_profile", "You need at least one profile.")
    await db.delete(profile)
    await db.commit()


@router.post("/preview", response_model=PreviewOut)
async def preview_rules(
    body: PreviewIn,
    current: CurrentUserDep,
    db: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PreviewOut:
    """What the last `hours` would have looked like with these rules. Nothing is saved."""
    profiles.validate_thresholds(body.show_threshold, body.alert_threshold)
    names = await profiles.source_slugs(db)
    rules = profiles.validate_rules([r.to_input() for r in body.rules], set(names))
    compiled = CompiledProfile(
        id=None,
        version=0,
        rules=[profiles.to_spec(r) for r in rules],
        show_threshold=body.show_threshold,
        alert_threshold=body.alert_threshold,
        source_names=names,
    )
    results = await preview(db, compiled, body.hours)
    shown = [(s, r) for s, r in results if r.score >= body.show_threshold]
    return PreviewOut(
        hours=body.hours,
        shown=len(shown),
        alerts=sum(1 for _, r in shown if r.alert_score >= body.alert_threshold),
        by_tier=dict(Counter(r.tier for _, r in results if r.tier)),
        stories=[
            PreviewStoryOut(
                story_id=s.id, title=s.title, url=s.url, published_at=s.first_published_at,
                score=r.score, tier=r.tier, pinned=r.pinned,
                reasons=[x.as_dict() for x in r.reasons],
            )
            for s, r in shown[:limit]
        ],
    )  # fmt: skip
