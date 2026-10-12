import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceKind(enum.StrEnum):
    RSS = "rss"  # RSS 2.0, RSS 1.0 (RDF) and Atom
    GOOGLE_NEWS = "google_news"  # a Google News search feed (many publishers)


class LicenceClass(enum.StrEnum):
    OFFICIAL = "official"  # government / regulator / exchange releases
    HEADLINE_LINK = "headline_link"  # publisher feed: show headline + link only
    LICENSED = "licensed"  # paid licence; display rights per contract


class SourceStatus(enum.StrEnum):
    UNKNOWN = "unknown"  # never polled
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # failing, being retried with back-off
    FAILING = "failing"  # failing for a long time; needs an admin to look


class FetchOutcome(enum.StrEnum):
    OK = "ok"
    NOT_MODIFIED = "not_modified"
    ERROR = "error"


def _enum_values(e: type[enum.StrEnum]) -> str:
    return ", ".join(f"'{m.value}'" for m in e)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_enum_values(SourceKind)})", name="kind"),
        CheckConstraint(f"licence_class IN ({_enum_values(LicenceClass)})", name="licence_class"),
        CheckConstraint(f"status IN ({_enum_values(SourceStatus)})", name="status"),
        CheckConstraint("authority BETWEEN 1 AND 5", name="authority"),
        CheckConstraint("poll_interval_seconds >= 15", name="poll_interval"),
        Index("ix_sources_due", "next_poll_at", postgresql_where=text("enabled")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    homepage: Mapped[str | None] = mapped_column(Text)

    category: Mapped[str] = mapped_column(String(40))  # e.g. official_india, press_global
    region: Mapped[str] = mapped_column(String(20))  # IN, US, GLOBAL
    licence_class: Mapped[str] = mapped_column(String(20))
    # 1 (minor blog) .. 5 (the issuing authority itself, e.g. RBI). Feeds the importance score.
    authority: Mapped[int] = mapped_column(SmallInteger)
    is_priority: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    poll_interval_seconds: Mapped[int] = mapped_column(Integer)

    # Polling state
    status: Mapped[str] = mapped_column(String(20), server_default=SourceStatus.UNKNOWN.value)
    consecutive_failures: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_item_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # HTTP conditional-request validators, so unchanged feeds cost a 304 and no parsing.
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SourceFetch(Base):
    """One row per poll attempt: the raw material for the ingestion health page."""

    __tablename__ = "source_fetches"
    __table_args__ = (
        CheckConstraint(f"outcome IN ({_enum_values(FetchOutcome)})", name="outcome"),
        Index("ix_source_fetches_source_started", "source_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    items_seen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    items_new: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
