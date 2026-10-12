import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeliveryStatus(enum.StrEnum):
    PENDING = "pending"  # queued for this channel
    SENT = "sent"
    FAILED = "failed"  # gave up after retries
    SKIPPED = "skipped"  # channel not connected or switched off
    QUIET = "quiet"  # held back by quiet hours


def _values(e: type[enum.StrEnum]) -> str:
    return ", ".join(f"'{m.value}'" for m in e)


class Alert(Base):
    """A story that crossed a user's alert line. One per user per event:
    a second story about the same event within the grouping window is not alerted again."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("uq_alerts_user_story", "user_id", "story_id", unique=True),
        Index("ix_alerts_user_created", "user_id", text("created_at DESC")),
        Index(
            "ix_alerts_telegram_pending",
            "created_at",
            postgresql_where=text("telegram_status = 'pending'"),
        ),
        CheckConstraint(f"telegram_status IN ({_values(DeliveryStatus)})", name="telegram_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))

    # Snapshot at alert time: what the user was told, even if the story changes later.
    score: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(200))
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reasons: Mapped[list[dict]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    telegram_status: Mapped[str] = mapped_column(
        String(10), server_default=DeliveryStatus.SKIPPED.value
    )
    telegram_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    telegram_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_error: Mapped[str | None] = mapped_column(Text)


class TelegramLink(Base):
    """A user's connected Telegram chat. A chat belongs to at most one user."""

    __tablename__ = "telegram_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationSettings(Base):
    """Per-user delivery preferences. Quiet hours hold back Telegram alerts (not pinned ones)."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint(
            "quiet_start IS NULL OR quiet_start BETWEEN 0 AND 1439", name="quiet_start"
        ),
        CheckConstraint("quiet_end IS NULL OR quiet_end BETWEEN 0 AND 1439", name="quiet_end"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # Minutes after midnight in `timezone`; both NULL means no quiet hours.
    quiet_start: Mapped[int | None] = mapped_column(SmallInteger)
    quiet_end: Mapped[int | None] = mapped_column(SmallInteger)
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Kolkata")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Verdict(enum.StrEnum):
    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"


class StoryFeedback(Base):
    """'Relevant' / 'not relevant' on a story, per profile. The latest verdict wins."""

    __tablename__ = "story_feedback"
    __table_args__ = (
        Index("uq_story_feedback", "profile_id", "story_id", unique=True),
        Index("ix_story_feedback_profile_created", "profile_id", text("created_at DESC")),
        CheckConstraint(f"verdict IN ({_values(Verdict)})", name="verdict"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    story_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    verdict: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
