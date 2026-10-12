import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RuleKind(enum.StrEnum):
    TOPIC = "topic"
    ENTITY = "entity"
    EVENT_TYPE = "event_type"
    KEYWORD = "keyword"
    SOURCE = "source"
    REGION = "region"


class RuleMode(enum.StrEnum):
    INCLUDE = "include"  # adds relevance, by weight
    EXCLUDE = "exclude"  # hides matching stories (a mute when it has expires_at)
    PIN = "pin"  # guarantees at least `min_score`


def _values(e: type[enum.StrEnum]) -> str:
    return ", ".join(f"'{m.value}'" for m in e)


class Profile(Base):
    """A user's description of what matters: rules plus show/alert thresholds."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("show_threshold BETWEEN 20 AND 100", name="show_threshold"),
        CheckConstraint("alert_threshold BETWEEN 20 AND 100", name="alert_threshold"),
        Index("ix_profiles_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    template: Mapped[str | None] = mapped_column(String(40))  # template it started from
    show_threshold: Mapped[int] = mapped_column(SmallInteger, server_default=text("50"))
    alert_threshold: Mapped[int] = mapped_column(SmallInteger, server_default=text("80"))
    # Bumped on every rule/threshold change; scores remember which version produced them.
    version: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileRule(Base):
    """One line of a profile.

    kind/value say what to match (topic "monetary_policy", entity "rbi", keyword "repo rate").
    `entity` narrows an event_type rule to one actor: event_type "rate_decision" + entity
    "rbi" is "an RBI rate decision".
    """

    __tablename__ = "profile_rules"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_values(RuleKind)})", name="kind"),
        CheckConstraint(f"mode IN ({_values(RuleMode)})", name="mode"),
        CheckConstraint("weight BETWEEN 1 AND 10", name="weight"),
        CheckConstraint("min_score IS NULL OR min_score BETWEEN 20 AND 100", name="min_score"),
        # Candidate lookup when a story arrives: "which profiles care about entity rbi?"
        Index("ix_profile_rules_kind_value", "kind", "value"),
        Index("ix_profile_rules_profile_id", "profile_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str | None] = mapped_column(String(60))
    mode: Mapped[str] = mapped_column(String(10), server_default=RuleMode.INCLUDE.value)
    weight: Mapped[int] = mapped_column(SmallInteger, server_default=text("5"))
    min_score: Mapped[int | None] = mapped_column(SmallInteger)
    # Feedback (Phase 5) adjusts weights; remember where the user set it so it can be reset.
    base_weight: Mapped[int | None] = mapped_column(SmallInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StoryScore(Base):
    """A story's score for one profile, with the reasons. Written when the story or the
    profile changes, so reading a feed is a plain indexed query."""

    __tablename__ = "story_scores"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score"),
        Index(
            "ix_story_scores_feed",
            "profile_id",
            text("score DESC"),
            text("story_published_at DESC"),
        ),
        Index("ix_story_scores_latest", "profile_id", text("story_published_at DESC")),
        Index("ix_story_scores_story_id", "story_id"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(SmallInteger)
    # Score from rules alone when an AI adjustment changed `score`; alerts use it. NULL = same.
    rule_score: Mapped[int | None] = mapped_column(SmallInteger)
    relevance: Mapped[int] = mapped_column(SmallInteger)
    importance: Mapped[int] = mapped_column(SmallInteger)  # factor x100, 50..100
    pinned: Mapped[bool] = mapped_column(server_default=text("false"))
    reasons: Mapped[list[dict]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Copied from the story so the feed can sort without a join.
    story_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    profile_version: Mapped[int] = mapped_column(SmallInteger)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
