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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Plan(Base):
    """A plan and what it allows. Data, not code: features and prices change without a deploy.

    The Razorpay plan ids are configuration (RAZORPAY_PLAN_<CODE>), not stored here, because
    test mode and live mode have different ids.
    """

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # free, pro, pro_plus
    name: Mapped[str] = mapped_column(String(40))
    tagline: Mapped[str | None] = mapped_column(String(200))
    # Shown on the pricing page. What is charged is the Razorpay plan's amount: keep them equal.
    price_paise: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    interval: Mapped[str] = mapped_column(String(10), server_default="month")
    # feed_delay_minutes, max_profiles, telegram, ai_summaries, agent, max_saved (null = no cap)
    features: Mapped[dict] = mapped_column(JSONB)
    rank: Mapped[int] = mapped_column(SmallInteger)  # higher = more; decides upgrade vs downgrade
    is_public: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Subscription(Base):
    """Our copy of a Razorpay subscription. Razorpay is the source of truth; webhooks, the
    post-checkout verification and a periodic sync keep this copy current."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("uq_subscriptions_razorpay_id", "razorpay_subscription_id", unique=True),
        Index("ix_subscriptions_user_id", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    razorpay_subscription_id: Mapped[str] = mapped_column(String(40))
    # Razorpay's status: created, authenticated, active, pending, halted, cancelled,
    # completed, expired, paused.
    status: Mapped[str] = mapped_column(String(20))
    current_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # A downgrade waits for the end of the paid period.
    scheduled_plan_code: Mapped[str | None] = mapped_column(String(20))
    short_url: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Payment(Base):
    """One charge (first payment or a renewal). Razorpay issues the GST invoice for it."""

    __tablename__ = "payments"
    __table_args__ = (
        Index("uq_payments_razorpay_id", "razorpay_payment_id", unique=True),
        Index("ix_payments_user_id", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String(40))
    razorpay_invoice_id: Mapped[str | None] = mapped_column(String(40))
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    status: Mapped[str] = mapped_column(String(20))  # captured, failed, refunded, ...
    method: Mapped[str | None] = mapped_column(String(20))  # upi, card, netbanking, ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BillingEvent(Base):
    """Every webhook we accepted, once: makes redelivery harmless and leaves an audit trail."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # X-Razorpay-Event-Id
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
