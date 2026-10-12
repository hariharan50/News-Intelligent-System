"""Plans, the user's subscription, Razorpay Checkout and the Razorpay webhook."""

import json
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import CurrentUserDep
from app.billing import service as billing
from app.billing.plans import all_plans, entitlements
from app.billing.razorpay import RazorpayClient, verify_webhook_signature
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.db.session import SessionDep
from app.ingestion import http
from app.models import Payment

log = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

PlanCode = Literal["free", "pro", "pro_plus"]


def razorpay() -> RazorpayClient:
    """Indirection so tests can replace the Razorpay client."""
    return RazorpayClient(http=http.get_client())


class PlanOut(BaseModel):
    code: str
    name: str
    tagline: str | None
    price_paise: int
    interval: str
    features: dict
    purchasable: bool  # a Razorpay plan is configured for it


class SubscriptionOut(BaseModel):
    plan: str
    status: str
    current_end: datetime | None
    cancel_at_period_end: bool
    scheduled_plan: str | None
    in_grace: bool  # a renewal failed; Razorpay is retrying


class PaymentOut(BaseModel):
    id: uuid.UUID
    amount_paise: int
    currency: str
    status: str
    method: str | None
    created_at: datetime
    has_invoice: bool


class BillingOut(BaseModel):
    enabled: bool  # FEATURE_BILLING: plans and payments exist at all
    configured: bool
    current: dict  # Entitlements.as_dict()
    subscription: SubscriptionOut | None
    payments: list[PaymentOut]


class PlanIn(BaseModel):
    plan: PlanCode


class VerifyIn(BaseModel):
    razorpay_payment_id: str = Field(max_length=40)
    razorpay_subscription_id: str = Field(max_length=40)
    razorpay_signature: str = Field(max_length=128)


def _require_billing() -> None:
    if not get_settings().feature_billing:
        raise HTTPException(
            404, detail={"code": "billing_disabled", "message": "Plans and payments are off."}
        )


@router.get("/plans", response_model=list[PlanOut])
async def plans(db: SessionDep) -> list[PlanOut]:
    """Public: the pricing page. Empty when billing is switched off."""
    if not get_settings().feature_billing:
        return []
    ids = get_settings().razorpay_plan_ids()
    configured = get_settings().billing_configured
    return [
        PlanOut(
            code=p.code, name=p.name, tagline=p.tagline, price_paise=p.price_paise,
            interval=p.interval, features=p.features,
            purchasable=configured and p.code in ids,
        )
        for p in (await all_plans(db)).values()
        if p.is_public
    ]  # fmt: skip


@router.get("", response_model=BillingOut)
async def my_billing(current: CurrentUserDep, db: SessionDep) -> BillingOut:
    ent = await entitlements(db, current.user_id)
    sub = ent.subscription
    payments = await db.scalars(
        select(Payment)
        .where(Payment.user_id == current.user_id)
        .order_by(Payment.created_at.desc())
        .limit(24)
    )
    return BillingOut(
        enabled=get_settings().feature_billing,
        configured=get_settings().billing_configured,
        current=ent.as_dict(),
        subscription=SubscriptionOut(
            plan=sub.plan_code,
            status=sub.status,
            current_end=sub.current_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            scheduled_plan=sub.scheduled_plan_code,
            in_grace=sub.status in ("pending", "halted"),
        )
        if sub
        else None,
        payments=[
            PaymentOut(
                id=p.id, amount_paise=p.amount_paise, currency=p.currency, status=p.status,
                method=p.method, created_at=p.created_at, has_invoice=bool(p.razorpay_invoice_id),
            )
            for p in payments
        ],
    )  # fmt: skip


@router.post("/checkout")
async def checkout(body: PlanIn, current: CurrentUserDep, db: SessionDep) -> dict:
    """Options for Razorpay Checkout (subscription id, public key id, prefill)."""
    _require_billing()
    return await billing.start_checkout(db, current.user_id, body.plan, razorpay())


@router.post("/verify", response_model=BillingOut)
async def verify(body: VerifyIn, current: CurrentUserDep, db: SessionDep) -> BillingOut:
    _require_billing()
    await billing.verify_checkout(
        db, redis_client, current.user_id,
        payment_id=body.razorpay_payment_id, subscription_id=body.razorpay_subscription_id,
        signature=body.razorpay_signature, rzp=razorpay(),
    )  # fmt: skip
    return await my_billing(current, db)


@router.post("/cancel", response_model=BillingOut)
async def cancel(current: CurrentUserDep, db: SessionDep) -> BillingOut:
    _require_billing()
    await billing.cancel(db, redis_client, current.user_id, razorpay())
    return await my_billing(current, db)


@router.post("/change-plan", response_model=BillingOut)
async def change_plan(body: PlanIn, current: CurrentUserDep, db: SessionDep) -> BillingOut:
    _require_billing()
    await billing.change_plan(db, redis_client, current.user_id, body.plan, razorpay())
    return await my_billing(current, db)


@router.get("/payments/{payment_id}/invoice")
async def invoice(payment_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> dict:
    return {"url": await billing.invoice_url(db, current.user_id, payment_id, razorpay())}


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: SessionDep) -> dict:
    """Razorpay webhook. Authenticated by its HMAC signature, not by a user session."""
    _require_billing()
    body = await request.body()
    if not verify_webhook_signature(body, request.headers.get("x-razorpay-signature")):
        raise HTTPException(400, detail={"code": "bad_signature", "message": "Bad signature."})
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_body", "message": "Not JSON."}) from None
    event_id = request.headers.get("x-razorpay-event-id") or (
        f"{payload.get('event')}:{payload.get('created_at')}:"
        f"{((payload.get('payload') or {}).get('subscription') or {}).get('entity', {}).get('id')}"
    )
    result = await billing.handle_webhook(db, redis_client, event_id, payload)
    return {"status": result}
