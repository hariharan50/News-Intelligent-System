import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, SmallInteger, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailOutbox(Base):
    """Emails waiting to be sent, and a record of those sent. The worker sends them with
    retries, so a slow or failing mail server never slows down or breaks a request."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        Index("ix_email_outbox_pending", "next_attempt_at",
              postgresql_where=sql_text("status = 'pending'")),
        # One reminder per occasion, e.g. "trial ending" once per trial.
        Index("uq_email_outbox_dedupe", "dedupe_key", unique=True,
              postgresql_where=sql_text("dedupe_key IS NOT NULL")),
    )  # fmt: skip

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    to_address: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(200))
    text_body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10), server_default="pending")  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=sql_text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailToken(Base):
    """A single-use link token (verify email, reset password). Only its SHA-256 is stored."""

    __tablename__ = "email_tokens"
    __table_args__ = (
        Index("uq_email_tokens_hash", "token_hash", unique=True),
        Index("ix_email_tokens_user", "user_id", "purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))  # verify_email | reset_password
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    # For verify_email: the address it proves (the account's email could change later).
    email: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
