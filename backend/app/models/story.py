import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Story(Base):
    """One real-world event, grouping every article that reports it.

    The aggregate columns are derived from the story's articles by
    processing.service.refresh_stories(); never edit them by hand.
    """

    __tablename__ = "stories"
    __table_args__ = (
        Index("ix_stories_last_published_at", text("last_published_at DESC")),
        Index("ix_stories_entities", "entities", postgresql_using="gin"),
        Index("ix_stories_topics", "topics", postgresql_using="gin"),
        Index("ix_stories_event_type", "event_type", "first_published_at"),
        # Feed text search: match_text ILIKE '%term%' uses this trigram index.
        Index(
            "ix_stories_match_text_trgm",
            "match_text",
            postgresql_using="gin",
            postgresql_ops={"match_text": "gin_trgm_ops"},
        ),
        Index(
            "ix_stories_needs_scoring",
            "updated_at",
            postgresql_where=text("scored_at IS NULL OR scored_at < updated_at"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # Headline and link of the lead article: the most authoritative source, earliest first.
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    lead_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    # The lead article's own publisher when it came through an aggregator (Google News).
    lead_publisher: Mapped[str | None] = mapped_column(String(120))

    event_type: Mapped[str | None] = mapped_column(String(40))
    is_routine: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), server_default=text("'{}'::varchar[]")
    )
    entities: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), server_default=text("'{}'::varchar[]")
    )
    regions: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), server_default=text("'{}'::varchar[]")
    )

    # Inputs for keyword and source rules: lower-cased titles and excerpts of all articles
    # (capped), and the slugs of every source carrying the story.
    match_text: Mapped[str] = mapped_column(Text, server_default="")
    source_slugs: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'::varchar[]")
    )

    article_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    source_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # Highest authority (1-5) among the story's sources; feeds the importance score.
    max_authority: Mapped[int] = mapped_column(SmallInteger)
    has_official_source: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    first_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Claude's market-impact rating (1 minor .. 5 very major); NULL until checked.
    ai_impact: Mapped[int | None] = mapped_column(SmallInteger)
    # Set by scoring. NULL or older than updated_at means profiles haven't seen this version.
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
