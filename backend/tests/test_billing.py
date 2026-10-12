"""Plans, trials, limits and Razorpay subscriptions (needs Docker PostgreSQL/Redis).

Razorpay is replaced by a small in-memory fake behind httpx.MockTransport; signatures are
computed exactly as Razorpay does, so verification code runs for real.
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.api import billing as billing_api
from app.billing.plans import subscription_grants
from app.billing.razorpay import RazorpayClient
from app.billing.service import sync_subscriptions
from app.core.config import get_settings
from app.models import Alert, BillingEvent, Payment, Subscription, User
from tests.test_alerts_telegram_admin import connect_telegram, rbi_source, telegram_on  # noqa: F401
from tests.test_profiles_feed_api import add_article, pipeline, sign_up

pytestmark = pytest.mark.usefixtures("db")

KEY_SECRET = "test_key_secret_123"
WEBHOOK_SECRET = "whsec_test_456"


class FakeRazorpay:
    """Just enough of the Razorpay subscriptions API."""

    def __init__(self) -> None:
        self.subs: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_change = False

    def charge(self, sub_id: str, *, days: int = 30, invoice: str = "inv_1") -> str:
        """Simulate a successful first charge: subscription active, payment captured."""
        now = int(time.time())
        sub = self.subs[sub_id]
        sub.update(status="active", current_start=now, current_end=now + days * 86400)
        pay_id = f"pay_{uuid.uuid4().hex[:14]}"
        self.payments[pay_id] = {
            "id": pay_id, "amount": 49900, "currency": "INR", "status": "captured",
            "method": "upi", "invoice_id": invoice, "created_at": now,
        }  # fmt: skip
        return pay_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        parts = path.strip("/").split("/")
        if request.method == "POST" and path == "/subscriptions":
            sub_id = f"sub_{uuid.uuid4().hex[:14]}"
            self.subs[sub_id] = {
                "id": sub_id, "entity": "subscription", "plan_id": body["plan_id"],
                "status": "created", "current_start": None, "current_end": None,
                "notes": body["notes"], "short_url": f"https://rzp.io/i/{sub_id}",
            }  # fmt: skip
            return httpx.Response(200, json=self.subs[sub_id])
        if parts[0] == "subscriptions" and len(parts) >= 2:
            sub = self.subs.get(parts[1])
            if sub is None:
                return httpx.Response(400, json={"error": {"description": "no such id"}})
            if request.method == "GET":
                return httpx.Response(200, json=sub)
            if request.method == "POST" and parts[-1] == "cancel":
                if not body["cancel_at_cycle_end"]:
                    sub["status"] = "cancelled"
                return httpx.Response(200, json=sub)
            if request.method == "PATCH":
                if self.fail_change:
                    return httpx.Response(
                        400, json={"error": {"description": "Plan change not allowed for UPI"}}
                    )
                if body["schedule_change_at"] == "now":
                    sub["plan_id"] = body["plan_id"]
                return httpx.Response(200, json=sub)
        if parts[0] == "payments":
            return httpx.Response(200, json=self.payments[parts[1]])
        if parts[0] == "invoices":
            return httpx.Response(
                200, json={"id": parts[1], "short_url": f"https://rzp.io/inv/{parts[1]}"}
            )
        return httpx.Response(404, json={"error": {"description": "unknown"}})


@pytest.fixture
def rzp(monkeypatch: pytest.MonkeyPatch) -> FakeRazorpay:
    s = get_settings()
    for name, value in {
        "razorpay_key_id": "rzp_test_abc", "razorpay_key_secret": KEY_SECRET,
        "razorpay_webhook_secret": WEBHOOK_SECRET, "razorpay_plan_pro": "plan_PRO",
        "razorpay_plan_pro_plus": "plan_PROPLUS", "billing_require_verified_email": False,
    }.items():  # fmt: skip
        monkeypatch.setattr(s, name, value)
    fake = FakeRazorpay()
    client = RazorpayClient(http=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(billing_api, "razorpay", lambda: client)
    fake.client = client
    return fake


def checkout_signature(payment_id: str, subscription_id: str) -> str:
    message = f"{payment_id}|{subscription_id}".encode()
    return hmac.new(KEY_SECRET.encode(), message, hashlib.sha256).hexdigest()


async def end_trial(db, email: str = "trader@example.com") -> None:
    async with db() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.email == email)
            .values(trial_ends_at=datetime.now(UTC) - timedelta(minutes=1))
        )


async def buy(client, headers, rzp: FakeRazorpay, plan: str = "pro") -> dict:
    opts = (await client.post("/api/billing/checkout", json={"plan": plan}, headers=headers)).json()
    pay_id = rzp.charge(opts["subscription_id"])
    resp = await client.post(
        "/api/billing/verify",
        json={"razorpay_payment_id": pay_id, "razorpay_subscription_id": opts["subscription_id"],
              "razorpay_signature": checkout_signature(pay_id, opts["subscription_id"])},
        headers=headers,
    )  # fmt: skip
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- plans & trial


async def test_public_plans_and_new_accounts_start_a_pro_trial(api, rzp) -> None:
    client = api()
    plans = (await client.get("/api/billing/plans")).json()
    assert [p["code"] for p in plans] == ["free", "pro", "pro_plus"]
    assert plans[1]["price_paise"] == 49900 and plans[1]["purchasable"] is True
    assert plans[0]["purchasable"] is False  # free is not bought

    headers = await sign_up(client)
    me = (await client.get("/api/billing", headers=headers)).json()
    assert me["current"]["plan"] == "pro" and me["current"]["source"] == "trial"
    trial_end = datetime.fromisoformat(me["current"]["trial_ends_at"])
    assert timedelta(days=6, hours=23) < trial_end - datetime.now(UTC) <= timedelta(days=7)


async def test_plans_say_not_purchasable_without_keys(api) -> None:
    plans = (await api().get("/api/billing/plans")).json()
    assert not any(p["purchasable"] for p in plans)
    client = api()
    headers = await sign_up(client)
    resp = await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=headers)
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "billing_not_configured"


# --------------------------------------------------------------------------- free plan limits


async def test_free_plan_limits(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    await end_trial(db)

    me = (await client.get("/api/billing", headers=headers)).json()
    assert me["current"]["plan"] == "free"

    # One profile (the one created at sign-up).
    resp = await client.post(
        "/api/profiles", json={"name": "Second", "show_threshold": 50, "alert_threshold": 80, "rules": []},
        headers=headers,
    )  # fmt: skip
    assert resp.status_code == 403
    assert resp.json()["detail"] == {
        "code": "profile_limit", "message": resp.json()["detail"]["message"], "needs": "pro",
    }  # fmt: skip

    # News is 15 minutes late on the free feed.
    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 5)
    await add_article(db, rbi, "RBI cuts CRR by 50 basis points to 3.5%", 30)
    await pipeline(db)
    feed = (await client.get("/api/feed", headers=headers)).json()
    titles = [i["title"] for i in feed["items"]]
    assert feed["delay_minutes"] == 15
    assert "RBI cuts CRR by 50 basis points to 3.5%" in titles
    assert not any(t.startswith("RBI keeps repo rate") for t in titles)


async def test_free_plan_saved_cap(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    await end_trial(db)
    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI cuts CRR by 50 basis points to 3.5%", 30)
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 40
    )
    await pipeline(db)
    feed = (await client.get("/api/feed", headers=headers)).json()
    first, second = (i["story_id"] for i in feed["items"][:2])

    from app.models import Plan

    async with db() as session, session.begin():
        plan = await session.get(Plan, "free")
        original = dict(plan.features)
        plan.features = {**original, "max_saved": 1}
    try:
        assert (await client.put(f"/api/stories/{first}/saved", headers=headers)).status_code == 204
        # Saving the same story again isn't a second slot.
        assert (await client.put(f"/api/stories/{first}/saved", headers=headers)).status_code == 204
        resp = await client.put(f"/api/stories/{second}/saved", headers=headers)
        assert resp.status_code == 403 and resp.json()["detail"]["code"] == "saved_limit"
    finally:
        async with db() as session, session.begin():
            (await session.get(Plan, "free")).features = original


async def test_telegram_is_a_paid_feature(api, db, telegram_on) -> None:  # noqa: F811
    client = api()
    headers = await sign_up(client)
    await connect_telegram(api, db, client, headers)  # allowed during the trial
    await end_trial(db)
    resp = await client.post("/api/notifications/telegram/link", headers=headers)
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "telegram_plan"

    # Alerts still arrive in the app, but aren't sent to Telegram on the free plan.
    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 5)
    await pipeline(db)
    async with db() as session:
        [alert] = list(await session.scalars(select(Alert)))
    assert alert.telegram_status == "skipped"


async def test_ai_summaries_are_locked_on_free(api, db) -> None:
    from app.ai import service as ai
    from app.cache.redis import redis_client
    from tests.test_ai_summaries import FakeSummarizer

    client = api()
    headers = await sign_up(client)
    rbi = await rbi_source(db)
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 30
    )
    await pipeline(db)
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer())

    [item] = (await client.get("/api/feed", headers=headers)).json()["items"]
    assert item["ai"] is not None and item["ai_locked"] is False
    await end_trial(db)
    [item] = (await client.get("/api/feed", headers=headers)).json()["items"]
    assert item["ai"] is None and item["ai_locked"] is True


# --------------------------------------------------------------------------- buying


async def test_checkout_verify_and_billing_page(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await end_trial(db)

    opts = (
        await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=headers)
    ).json()
    assert opts["key_id"] == "rzp_test_abc" and opts["subscription_id"].startswith("sub_")
    assert opts["prefill"]["email"] == "trader@example.com"
    assert rzp.calls[0][2]["plan_id"] == "plan_PRO"
    assert rzp.calls[0][2]["notes"]["plan"] == "pro"
    # Opening checkout again reuses the unpaid subscription.
    again = (
        await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=headers)
    ).json()
    assert again["subscription_id"] == opts["subscription_id"]

    # A forged signature is refused, and nothing changes.
    pay_id = rzp.charge(opts["subscription_id"])
    bad = await client.post(
        "/api/billing/verify",
        json={"razorpay_payment_id": pay_id, "razorpay_subscription_id": opts["subscription_id"],
              "razorpay_signature": "0" * 64},
        headers=headers,
    )  # fmt: skip
    assert bad.status_code == 400
    assert (await client.get("/api/billing", headers=headers)).json()["current"]["plan"] == "free"

    me = (
        await client.post(
            "/api/billing/verify",
            json={"razorpay_payment_id": pay_id, "razorpay_subscription_id": opts["subscription_id"],
                  "razorpay_signature": checkout_signature(pay_id, opts["subscription_id"])},
            headers=headers,
        )
    ).json()  # fmt: skip
    assert me["current"]["plan"] == "pro" and me["current"]["source"] == "subscription"
    assert me["subscription"]["status"] == "active"
    [payment] = me["payments"]
    assert (
        payment["amount_paise"] == 49900 and payment["method"] == "upi" and payment["has_invoice"]
    )

    invoice = (
        await client.get(f"/api/billing/payments/{payment['id']}/invoice", headers=headers)
    ).json()
    assert invoice["url"] == "https://rzp.io/inv/inv_1"

    # Already subscribed: checkout for another plan is refused (change plan instead).
    resp = await client.post("/api/billing/checkout", json={"plan": "pro_plus"}, headers=headers)
    assert resp.status_code == 409


async def test_other_users_cannot_verify_or_read_invoices(api, db, rzp) -> None:
    alice, bob = api(), api()
    alice_h = await sign_up(alice, "alice@example.com")
    bob_h = await sign_up(bob, "bob@example.com")
    opts = (await alice.post("/api/billing/checkout", json={"plan": "pro"}, headers=alice_h)).json()
    pay_id = rzp.charge(opts["subscription_id"])
    resp = await bob.post(
        "/api/billing/verify",
        json={"razorpay_payment_id": pay_id, "razorpay_subscription_id": opts["subscription_id"],
              "razorpay_signature": checkout_signature(pay_id, opts["subscription_id"])},
        headers=bob_h,
    )  # fmt: skip
    assert resp.status_code == 404
    await alice.post(
        "/api/billing/verify",
        json={"razorpay_payment_id": pay_id, "razorpay_subscription_id": opts["subscription_id"],
              "razorpay_signature": checkout_signature(pay_id, opts["subscription_id"])},
        headers=alice_h,
    )  # fmt: skip
    payment_id = (await alice.get("/api/billing", headers=alice_h)).json()["payments"][0]["id"]
    resp = await bob.get(f"/api/billing/payments/{payment_id}/invoice", headers=bob_h)
    assert resp.status_code == 404


async def test_upgrade_now_downgrade_at_period_end_and_cancel(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await buy(client, headers, rzp, "pro")

    up = (
        await client.post("/api/billing/change-plan", json={"plan": "pro_plus"}, headers=headers)
    ).json()
    assert up["current"]["plan"] == "pro_plus" and up["subscription"]["scheduled_plan"] is None

    down = (
        await client.post("/api/billing/change-plan", json={"plan": "pro"}, headers=headers)
    ).json()
    assert down["current"]["plan"] == "pro_plus"  # until the paid period ends
    assert down["subscription"]["scheduled_plan"] == "pro"
    assert rzp.calls[-1][2]["schedule_change_at"] == "cycle_end"

    rzp.fail_change = True
    resp = await client.post("/api/billing/change-plan", json={"plan": "pro_plus"}, headers=headers)
    assert resp.status_code == 409  # same plan as current (scheduled change pending)
    rzp.fail_change = False

    cancelled = (await client.post("/api/billing/cancel", headers=headers)).json()
    assert cancelled["subscription"]["cancel_at_period_end"] is True
    assert cancelled["current"]["plan"] == "pro_plus"  # paid until the end of the period
    resp = await client.post("/api/billing/change-plan", json={"plan": "pro"}, headers=headers)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "cancelling"


async def test_plan_change_refused_by_razorpay_is_explained(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await buy(client, headers, rzp, "pro")
    rzp.fail_change = True
    resp = await client.post("/api/billing/change-plan", json={"plan": "pro_plus"}, headers=headers)
    assert resp.status_code == 502
    assert "Plan change not allowed for UPI" in resp.json()["detail"]["message"]


# --------------------------------------------------------------------------- webhooks


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-razorpay-signature": sig, "x-razorpay-event-id": f"evt_{uuid.uuid4().hex[:12]}",
                 "content-type": "application/json"}  # fmt: skip


def event(name: str, sub: dict, payment: dict | None = None) -> dict:
    payload = {"subscription": {"entity": sub}}
    if payment:
        payload["payment"] = {"entity": payment}
    return {"entity": "event", "event": name, "payload": payload, "created_at": int(time.time())}


async def test_webhooks_renewal_failure_grace_and_drop_to_free(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await end_trial(db)
    await buy(client, headers, rzp, "pro")
    sub_id = next(iter(rzp.subs))
    sub = rzp.subs[sub_id]

    # A bad signature is rejected.
    raw, h = signed(event("subscription.charged", sub))
    h["x-razorpay-signature"] = "f" * 64
    assert (await client.post("/api/billing/webhook", content=raw, headers=h)).status_code == 400

    # Renewal charged: a new payment, period extended. Redelivery is harmless.
    now = int(time.time())
    sub.update(current_start=now, current_end=now + 60 * 86400)
    renewal = {"id": "pay_renewal1", "amount": 49900, "currency": "INR", "status": "captured",
               "method": "upi", "invoice_id": "inv_2", "created_at": now}  # fmt: skip
    raw, h = signed(event("subscription.charged", sub, renewal))
    assert (await client.post("/api/billing/webhook", content=raw, headers=h)).json() == {
        "status": "processed"
    }
    assert (await client.post("/api/billing/webhook", content=raw, headers=h)).json() == {
        "status": "duplicate"
    }
    me = (await client.get("/api/billing", headers=headers)).json()
    assert len(me["payments"]) == 2

    # Renewal failed: Razorpay retries (pending). Features stay during the grace period...
    sub.update(status="pending", current_end=now - 86400)
    raw, h = signed(event("subscription.pending", sub))
    await client.post("/api/billing/webhook", content=raw, headers=h)
    me = (await client.get("/api/billing", headers=headers)).json()
    assert me["current"]["plan"] == "pro" and me["subscription"]["in_grace"] is True

    # ...and end when retries are exhausted past the grace period.
    sub.update(status="halted", current_end=now - 4 * 86400)
    raw, h = signed(event("subscription.halted", sub))
    await client.post("/api/billing/webhook", content=raw, headers=h)
    me = (await client.get("/api/billing", headers=headers)).json()
    assert me["current"]["plan"] == "free"

    async with db() as session:
        events = list(await session.scalars(select(BillingEvent)))
    assert {e.event for e in events} == {
        "subscription.charged",
        "subscription.pending",
        "subscription.halted",
    }


async def test_webhook_for_unknown_subscription_is_ignored(api, rzp) -> None:
    client = api()
    sub = {"id": "sub_elsewhere", "plan_id": "plan_PRO", "status": "active", "notes": {}}
    raw, h = signed(event("subscription.activated", sub))
    assert (await client.post("/api/billing/webhook", content=raw, headers=h)).json() == {
        "status": "ignored"
    }


async def test_sync_catches_missed_webhooks(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await buy(client, headers, rzp, "pro")
    sub_id = next(iter(rzp.subs))
    rzp.subs[sub_id]["status"] = "cancelled"  # changed at Razorpay; no webhook reached us
    async with db() as session, session.begin():
        await session.execute(
            update(Subscription).values(synced_at=datetime.now(UTC) - timedelta(hours=1))
        )
    assert await sync_subscriptions(db, rzp.client) == 1
    async with db() as session:
        [sub] = list(await session.scalars(select(Subscription)))
    assert sub.status == "cancelled"


# --------------------------------------------------------------------------- unit


def test_grace_rules() -> None:
    now = datetime.now(UTC)
    sub = Subscription(status="pending", current_end=now - timedelta(days=2))
    assert subscription_grants(sub, now)  # inside the 3-day grace
    sub.current_end = now - timedelta(days=4)
    assert not subscription_grants(sub, now)
    sub = Subscription(status="cancelled", current_end=now + timedelta(days=5))
    assert subscription_grants(sub, now)  # paid until the end
    sub.current_end = now - timedelta(seconds=1)
    assert not subscription_grants(sub, now)
    assert not subscription_grants(Subscription(status="created", current_end=None), now)


async def test_deleting_an_account_removes_billing_rows(api, db, rzp) -> None:
    client = api()
    headers = await sign_up(client)
    await buy(client, headers, rzp, "pro")
    async with db() as session, session.begin():
        await session.execute(User.__table__.delete())
    async with db() as session:
        assert list(await session.scalars(select(Payment))) == []
        assert list(await session.scalars(select(Subscription))) == []
