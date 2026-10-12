import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SummaryStatus:
    OK = "ok"
    REFUSED = "refused"  # the model declined; not retried
    FAILED = "failed"  # API or output error; retried a few times


class StorySummary(Base):
    """Claude's summary of one story, written once and shared by every user.

    Built only from the headlines and feed excerpts we already store, never from scraped
    articles. Rewritten (a few times at most) when more reports join the story.
    """

    __tablename__ = "story_summaries"
    __table_args__ = (Index("ix_story_summaries_status", "status", "updated_at"),)

    story_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(12))
    summary: Mapped[str | None] = mapped_column(Text)
    key_facts: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Markets, sectors or assets the story may move. Shown labelled as AI opinion.
    affected: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # The story's article_count when this was written; a bigger count means "may be stale".
    article_count: Mapped[int] = mapped_column(Integer)
    revisions: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    model: Mapped[str | None] = mapped_column(String(60))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiUsageDaily(Base):
    """Tokens and estimated cost per day (India time) and feature. The daily budget reads it."""

    __tablename__ = "ai_usage_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    feature: Mapped[str] = mapped_column(String(20), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default=text("0"))
