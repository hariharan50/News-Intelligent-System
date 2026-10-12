import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Article(Base):
    """One item from one source. Processing tags it and attaches it to a Story.

    Duplicates are rejected by two unique keys:
      - (source_id, guid): the same feed item seen again on the next poll;
      - url_hash: the same canonical URL arriving through another feed of the same publisher.
    """

    __tablename__ = "articles"
    __table_args__ = (
        Index("uq_articles_source_guid", "source_id", "guid", unique=True),
        Index("uq_articles_url_hash", "url_hash", unique=True),
        Index("ix_articles_published_at", text("published_at DESC")),
        Index("ix_articles_title_hash", "title_hash"),
        Index(
            "ix_articles_unprocessed", "fetched_at", postgresql_where=text("processed_at IS NULL")
        ),
        Index("ix_articles_story_id", "story_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    guid: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(Text)  # canonical: tracking parameters removed
    url_hash: Mapped[bytes] = mapped_column(LargeBinary(32))  # sha256 of url
    title: Mapped[str] = mapped_column(Text)
    title_hash: Mapped[bytes] = mapped_column(LargeBinary(32))  # sha256 of normalised title
    # Feed-provided summary, plain text, truncated. Shown only where the licence allows;
    # also the only text AI summaries may be built from.
    excerpt: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    # Original publisher for aggregated sources (Google News: "Reuters"); NULL otherwise.
    publisher: Mapped[str | None] = mapped_column(String(120))
    feed_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), server_default=text("'{}'::varchar[]")
    )
    language: Mapped[str] = mapped_column(String(10), server_default="en")

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # True when the feed gave no usable date and published_at is the fetch time.
    published_at_estimated: Mapped[bool] = mapped_column(server_default=text("false"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set by processing: tags, the story this article belongs to, and when that happened.
    story_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL")
    )
    topics: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), server_default=text("'{}'::varchar[]")
    )
    entities: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), server_default=text("'{}'::varchar[]")
    )
    regions: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), server_default=text("'{}'::varchar[]")
    )
    event_type: Mapped[str | None] = mapped_column(String(40))
    # taxonomy.TAXONOMY_VERSION used to tag this article; lets us re-tag after changes.
    tag_version: Mapped[int | None] = mapped_column(SmallInteger)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
