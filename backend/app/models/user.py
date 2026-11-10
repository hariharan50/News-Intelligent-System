import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # One account per phone number. NULL for accounts created before phone was required.
        Index("uq_users_phone", "phone", unique=True, postgresql_where=text("phone IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))  # E.164, e.g. +919876543210
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Consent records: which terms were accepted and when, and optional marketing consent.
    terms_version: Mapped[str | None] = mapped_column(String(32))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # False for accounts created with Google: they hold a random, unknowable password until
    # the person sets one with "Forgot password".
    has_password: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Two-factor sign-in (authenticator app). The secret is encrypted (app.auth.totp);
    # totp_last_counter is the last accepted time step, so a code can't be replayed.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    # Free trial of the trial plan (Pro) until this time; NULL = no trial.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSession(Base):
    """One signed-in device. The JWT access token names it in its `sid` claim.

    The refresh token is random, sent only in an httpOnly cookie, and stored here as a
    SHA-256 hash. Each refresh rotates it; the previous hash is kept so that presenting an
    already-rotated token (a sign of theft) revokes the whole session.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index(
            "ix_user_sessions_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")
        ),
        Index("uq_user_sessions_token_hash", "token_hash", unique=True),
        Index("ix_user_sessions_previous_token_hash", "previous_token_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    previous_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # True only when the person ticked "Keep me signed in": the refresh cookie then has a
    # max-age and survives closing the browser. False means a cookie that dies with the
    # browser, so every refresh must re-issue it the same way.
    persistent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
