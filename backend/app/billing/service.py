"""Subscriptions: checkout, verification, webhooks, cancel/change, and a periodic sync.

Razorpay is the source of truth. We never trust the browser about a payment: after Checkout
we verify Razorpay's signature and then fetch the subscription from Razorpay ourselves.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.plans import FREE, GOOD_STANDING, all_plans, entitlements
from app.billing.razorpay import RazorpayClient, RazorpayError, verify_checkout_signature
from app.core.config import get_settings
from app.email import service as emails
from app.events import publish_to_user
from app.models import BillingEvent, Payment, Subscription, User

log = logging.getLogger(__name__)

# A checkout started but never paid is reused for this long, then a fresh one is created.
REUSE_UNPAID_FOR = timedelta(hours=1)
SYNC_EVERY = timedelta(minutes=30)


class BillingError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _plan_code_for(razorpay_plan_id: str | None) -> str | None:
    by_id = {pid: code for code, pid in get_settings().razorpay_plan_ids().items()}
    return by_id.get(razorpay_plan_id or "")


def _require_configured() -> None:
    if not get_settings().billing_configured:
        raise BillingError(503, "billing_not_configured", "Payments aren't set up yet.")


# --------------------------------------------------------------------------- storing


async def upsert_subscription(
    db: AsyncSession, entity: dict, *, user_id: uuid.UUID | None = None
) -> Subscription | None:
    """Apply a Razorpay subscription entity to our copy. Returns None for one we can't
    attribute to a user (not created by us)."""
    rzp_id = entity.get("id")
    if not rzp_id:
        return None
    sub = await db.scalar(
        select(Subscription).where(Subscription.razorpay_subscription_id == rzp_id)
    )
    plan_code = _plan_code_for(entity.get("plan_id"))
    if sub is None:
        notes = entity.get("notes") or {}
        try:
            owner = user_id or uuid.UUID(str(notes.get("user_id")))
        except ValueError:
            log.warning("subscription %s has no known owner; ignored", rzp_id)
            return None
        if await db.get(User, owner) is None:
            return None
        plan_code = plan_code or notes.get("plan")
        if plan_code not in await all_plans(db):
            log.warning("subscription %s is for an unknown plan; ignored", rzp_id)
            return None
        sub = Subscription(user_id=owner, plan_code=plan_code, razorpay_subscription_id=rzp_id)
        db.add(sub)

    if plan_code and plan_code != sub.plan_code:
        sub.plan_code = plan_code
    if sub.scheduled_plan_code == sub.plan_code:
        sub.scheduled_plan_code = None
    sub.status = entity.get("status") or sub.status
    sub.current_start = _ts(entity.get("current_start")) or sub.current_start
    sub.current_end = _ts(entity.get("current_end")) or sub.current_end
    sub.ended_at = _ts(entity.get("ended_at")) or sub.ended_at
    sub.short_url = entity.get("short_url") or sub.short_url
    if sub.status in ("cancelled", "completed", "expired"):
        sub.scheduled_plan_code = None
    sub.synced_at = sub.updated_at = datetime.now(UTC)
    await db.flush()
    return sub


async def record_payment(db: AsyncSession, sub: Subscription, entity: dict) -> None:
    if not entity.get("id"):
        return
    values = {
        "user_id": sub.user_id,
        "subscription_id": sub.id,
        "razorpay_payment_id": entity["id"],
        "razorpay_invoice_id": entity.get("invoice_id"),
        "amount_paise": int(entity.get("amount") or 0),
        "currency": entity.get("currency") or "INR",
        "status": entity.get("status") or "unknown",
        "method": entity.get("method"),
        "created_at": _ts(entity.get("created_at")) or datetime.now(UTC),
    }
    stmt = pg_insert(Payment).values(values)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[Payment.razorpay_payment_id],
            set_={"status": stmt.excluded.status,
                  "razorpay_invoice_id": stmt.excluded.razorpay_invoice_id},
        )
    )  # fmt: skip


# --------------------------------------------------------------------------- user actions


async def start_checkout(
    db: AsyncSession, user_id: uuid.UUID, plan_code: str, rzp: RazorpayClient
) -> dict:
    """Create (or reuse) an unpaid Razorpay subscription for Checkout to open."""
    _require_configured()
    plans = await all_plans(db)
    plan_id = get_settings().razorpay_plan_ids().get(plan_code)
    if plan_code == FREE or plan_code not in plans or not plan_id:
        raise BillingError(400, "unknown_plan", "That plan can't be bought.")
    user = await db.get(User, user_id)
    if get_settings().billing_require_verified_email and user.email_verified_at is None:
        raise BillingError(
            403, "email_unverified", "Confirm your email address first. We sent you a link."
        )
    current = await entitlements(db, user_id)
    if current.source == "subscription":
        raise BillingError(
            409, "already_subscribed", "You already have a subscription. Change plan instead."
        )

    recent = await db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.plan_code == plan_code,
            Subscription.status == "created",
            Subscription.created_at > datetime.now(UTC) - REUSE_UNPAID_FOR,
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    if recent is None:
        try:
            entity = await rzp.create_subscription(
                plan_id, notes={"user_id": str(user_id), "plan": plan_code}
            )
        except RazorpayError as exc:
            raise BillingError(502, "payment_provider_error", exc.message) from None
        recent = await upsert_subscription(db, entity, user_id=user_id)
        await db.commit()

    plan = plans[plan_code]
    return {
        "key_id": get_settings().razorpay_key_id,
        "subscription_id": recent.razorpay_subscription_id,
        "plan": plan_code,
        "name": plan.name,
        "amount_paise": plan.price_paise,
        "prefill": {"name": user.full_name or "", "email": user.email, "contact": user.phone or ""},
    }


async def verify_checkout(
    db: AsyncSession,
    redis: Redis,
    user_id: uuid.UUID,
    *,
    payment_id: str,
    subscription_id: str,
    signature: str,
    rzp: RazorpayClient,
) -> Subscription:
    _require_configured()
    if not verify_checkout_signature(payment_id, subscription_id, signature):
        raise BillingError(400, "bad_signature", "We couldn't verify that payment.")
    sub = await db.scalar(
        select(Subscription).where(
            Subscription.razorpay_subscription_id == subscription_id,
            Subscription.user_id == user_id,
        )
    )
    if sub is None:
        raise BillingError(404, "not_found", "Subscription not found.")
    try:
        entity = await rzp.fetch_subscription(subscription_id)
        payment = await rzp.fetch_payment(payment_id)
    except RazorpayError as exc:
        raise BillingError(502, "payment_provider_error", exc.message) from None
    sub = await upsert_subscription(db, entity, user_id=user_id)
    await record_payment(db, sub, payment)
    await db.commit()
    await publish_to_user(redis, user_id, {"type": "billing", "status": sub.status})
    return sub


async def _live_subscription(db: AsyncSession, user_id: uuid.UUID) -> Subscription:
    ent = await entitlements(db, user_id)
    sub = ent.subscription
    if sub is None or sub.status not in GOOD_STANDING:
        raise BillingError(409, "no_subscription", "You don't have an active subscription.")
    return sub


async def cancel(
    db: AsyncSession, redis: Redis, user_id: uuid.UUID, rzp: RazorpayClient
) -> Subscription:
    """Cancel at the end of the paid period: features stay until then, no refund needed."""
    _require_configured()
    sub = await _live_subscription(db, user_id)
    try:
        entity = await rzp.cancel_subscription(sub.razorpay_subscription_id, at_cycle_end=True)
    except RazorpayError as exc:
        raise BillingError(502, "payment_provider_error", exc.message) from None
    sub = await upsert_subscription(db, entity)
    sub.cancel_at_period_end = True
    sub.scheduled_plan_code = None
    await db.commit()
    await publish_to_user(redis, user_id, {"type": "billing", "status": sub.status})
    return sub


async def change_plan(
    db: AsyncSession, redis: Redis, user_id: uuid.UUID, plan_code: str, rzp: RazorpayClient
) -> Subscription:
    """Upgrade at once (Razorpay prorates) or downgrade at the end of the paid period.
    Moving to Free is a cancellation."""
    _require_configured()
    if plan_code == FREE:
        return await cancel(db, redis, user_id, rzp)
    sub = await _live_subscription(db, user_id)
    if sub.cancel_at_period_end:
        raise BillingError(
            409, "cancelling", "Your subscription ends soon; start a new one after it ends."
        )
    plans = await all_plans(db)
    plan_id = get_settings().razorpay_plan_ids().get(plan_code)
    if plan_code not in plans or not plan_id:
        raise BillingError(400, "unknown_plan", "That plan can't be bought.")
    if plan_code == sub.plan_code:
        raise BillingError(409, "same_plan", "You're already on that plan.")
    upgrade = plans[plan_code].rank > plans[sub.plan_code].rank
    try:
        entity = await rzp.change_plan(sub.razorpay_subscription_id, plan_id, now=upgrade)
    except RazorpayError as exc:
        # E.g. UPI Autopay mandates can't change amount: the user must cancel and resubscribe.
        raise BillingError(
            502, "plan_change_failed", f"Razorpay couldn't change the plan: {exc.message}"
        ) from None
    if not upgrade:
        # Razorpay keeps the old plan on the entity until the switch happens.
        entity = {k: v for k, v in entity.items() if k != "plan_id"}
    sub = await upsert_subscription(db, entity)
    if not upgrade:
        sub.scheduled_plan_code = plan_code
    await db.commit()
    await publish_to_user(redis, user_id, {"type": "billing", "status": sub.status})
    return sub


async def invoice_url(
    db: AsyncSession, user_id: uuid.UUID, payment_id: uuid.UUID, rzp: RazorpayClient
) -> str:
    _require_configured()
    payment = await db.get(Payment, payment_id)
    if payment is None or payment.user_id != user_id or not payment.razorpay_invoice_id:
        raise BillingError(404, "not_found", "No invoice for that payment.")
    try:
        invoice = await rzp.fetch_invoice(payment.razorpay_invoice_id)
    except RazorpayError as exc:
        raise BillingError(502, "payment_provider_error", exc.message) from None
    url = invoice.get("short_url")
    if not url:
        raise BillingError(404, "not_found", "The invoice isn't ready yet.")
    return url


# --------------------------------------------------------------------------- webhooks & sync

HANDLED_EVENTS = frozenset({
    "subscription.authenticated", "subscription.activated", "subscription.charged",
    "subscription.pending", "subscription.halted", "subscription.cancelled",
    "subscription.completed", "subscription.updated", "subscription.paused",
    "subscription.resumed",
})  # fmt: skip


async def handle_webhook(db: AsyncSession, redis: Redis, event_id: str, body: dict) -> str:
    """Apply one verified webhook. Returns "duplicate", "ignored" or "processed"."""
    event = str(body.get("event", ""))
    inserted = await db.scalar(
        pg_insert(BillingEvent)
        .values(id=event_id[:64], event=event[:60], payload=body)
        .on_conflict_do_nothing()
        .returning(BillingEvent.id)
    )
    if inserted is None:
        return "duplicate"
    if event not in HANDLED_EVENTS:
        await db.commit()
        return "ignored"

    payload = body.get("payload") or {}
    entity = (payload.get("subscription") or {}).get("entity") or {}
    sub = await upsert_subscription(db, entity)
    if sub is None:
        await db.commit()
        return "ignored"
    ends_later = sub.current_end is not None and sub.current_end > datetime.now(UTC)
    if event == "subscription.cancelled" and ends_later:
        sub.cancel_at_period_end = True
    payment = (payload.get("payment") or {}).get("entity")
    if payment:
        await record_payment(db, sub, payment)
    if event in ("subscription.pending", "subscription.halted"):
        user = await db.get(User, sub.user_id)
        plans = await all_plans(db)
        name = plans[sub.plan_code].name if sub.plan_code in plans else sub.plan_code
        period = f"{sub.current_end:%Y%m%d}" if sub.current_end else "none"
        await emails.notify_payment_failed(db, user, name, f"{sub.id}:{period}")
    await db.commit()
    await publish_to_user(redis, sub.user_id, {"type": "billing", "status": sub.status})
    log.info("billing webhook %s for subscription %s -> %s", event, sub.id, sub.status)
    return "processed"


async def sync_subscriptions(
    session_factory: async_sessionmaker[AsyncSession], rzp: RazorpayClient, *, limit: int = 50
) -> int:
    """Safety net for missed webhooks (and local development, where Razorpay can't reach us):
    re-fetch live subscriptions that haven't been synced recently or whose period ended."""
    if not get_settings().billing_configured:
        return 0
    now = datetime.now(UTC)
    async with session_factory() as db:
        due = list(
            await db.scalars(
                select(Subscription)
                .where(
                    Subscription.status.in_([*GOOD_STANDING, "created"]),
                    or_(
                        Subscription.synced_at.is_(None),
                        Subscription.synced_at < now - SYNC_EVERY,
                        Subscription.current_end < now,
                    ),
                )
                .order_by(Subscription.synced_at.nulls_first())
                .limit(limit)
            )
        )
        synced = 0
        for sub in due:
            # Unpaid checkouts older than a day are abandoned; stop polling them.
            if sub.status == "created" and sub.created_at < now - timedelta(days=1):
                sub.status = "expired"
                continue
            try:
                entity = await rzp.fetch_subscription(sub.razorpay_subscription_id)
            except RazorpayError:
                continue
            await upsert_subscription(db, entity)
            synced += 1
        await db.commit()
    return synced
