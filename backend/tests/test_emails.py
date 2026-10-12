"""Email verification, password reset, the outbox and billing emails (needs Docker
PostgreSQL/Redis). Mail is captured by a fake mailer; nothing is sent."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.cache.redis import redis_client
from app.core.config import get_settings
from app.email.sender import MailError, OutgoingEmail, build_message
from app.email.service import queue_trial_reminders, send_pending
from app.models import EmailOutbox, EmailToken, User
from tests.conftest import TEST_PASSWORD
from tests.test_billing import rzp  # noqa: F401  (fixture)
from tests.test_profiles_feed_api import sign_up

pytestmark = pytest.mark.usefixtures("db")

NEW_PASSWORD = "brand new secret 77"


class FakeMailer:
    def __init__(self, fail: MailError | None = None) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail = fail

    async def send(self, email: OutgoingEmail) -> None:
        if self.fail:
            raise self.fail
        self.sent.append(email)


async def deliver(db) -> FakeMailer:
    mailer = FakeMailer()
    await send_pending(db, redis=redis_client, mailer=mailer)
    return mailer


def token_in(email: OutgoingEmail, path: str) -> str:
    match = re.search(rf"{path}\?token=([\w-]+)", email.text)
    assert match, email.text
    return match.group(1)


# --------------------------------------------------------------------------- verification


async def test_signup_sends_a_verification_link_that_works_once(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    assert (await client.get("/api/auth/me", headers=headers)).json()["email_verified"] is False

    [mail] = (await deliver(db)).sent
    assert mail.to == "trader@example.com" and "Confirm" in mail.subject
    assert "http://127.0.0.1:8081/verify-email?token=" in mail.text  # APP_BASE_URL
    assert 'href="http://127.0.0.1:8081/verify-email?token=' in mail.html
    token = token_in(mail, "/verify-email")

    # The link works without being signed in (often opened on a phone).
    resp = await api().post("/api/auth/verify-email", json={"token": token})
    assert resp.status_code == 200 and resp.json()["verified"] is True
    assert (await client.get("/api/auth/me", headers=headers)).json()["email_verified"] is True

    again = await api().post("/api/auth/verify-email", json={"token": token})
    assert again.status_code == 400 and again.json()["detail"]["code"] == "invalid_link"
    # Nothing left to send.
    assert (await deliver(db)).sent == []


async def test_resend_replaces_the_old_link_and_is_rate_limited(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    first = token_in((await deliver(db)).sent[0], "/verify-email")
    resp = await client.post("/api/auth/resend-verification", headers=headers)
    assert resp.status_code == 202 and resp.json()["sent"] is True
    second = token_in((await deliver(db)).sent[0], "/verify-email")

    assert (await client.post("/api/auth/resend-verification", headers=headers)).status_code == 429
    assert (await api().post("/api/auth/verify-email", json={"token": first})).status_code == 400
    assert (await api().post("/api/auth/verify-email", json={"token": second})).status_code == 200
    resp = await client.post("/api/auth/resend-verification", headers=headers)
    assert resp.json() == {"sent": False, "already_verified": True}


async def test_expired_link_is_refused(api, db) -> None:
    client = api()
    await sign_up(client)
    token = token_in((await deliver(db)).sent[0], "/verify-email")
    async with db() as session, session.begin():
        await session.execute(
            update(EmailToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (await api().post("/api/auth/verify-email", json={"token": token})).status_code == 400


# --------------------------------------------------------------------------- password reset


async def test_forgot_password_does_not_reveal_accounts(api, db) -> None:
    client = api()
    resp = await client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert resp.status_code == 202 and resp.json() == {"sent": True}
    assert (await deliver(db)).sent == []


async def test_reset_password_flow(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    await deliver(db)  # the verification email

    resp = await api().post("/api/auth/forgot-password", json={"email": "Trader@Example.com"})
    assert resp.status_code == 202
    [mail] = (await deliver(db)).sent
    assert "Reset" in mail.subject
    token = token_in(mail, "/reset-password")

    # A weak password is refused and the link still works afterwards.
    weak = await api().post(
        "/api/auth/reset-password",
        json={"token": token, "password": "short", "confirm_password": "short"},
    )
    assert weak.status_code == 422
    resp = await api().post(
        "/api/auth/reset-password",
        json={"token": token, "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200, resp.text

    # Every device is signed out; the new password works, the old one doesn't.
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    login = api()
    old = await login.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": TEST_PASSWORD}
    )
    assert old.status_code == 401
    new = await login.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": NEW_PASSWORD}
    )
    assert new.status_code == 200
    assert new.json()["user"]["email_verified"] is True  # the link proved the address

    # The link is single-use, and a "password changed" notice went out.
    again = await api().post(
        "/api/auth/reset-password",
        json={
            "token": token,
            "password": NEW_PASSWORD + "x",
            "confirm_password": NEW_PASSWORD + "x",
        },
    )
    assert again.status_code == 400
    [notice] = (await deliver(db)).sent
    assert "password was changed" in notice.subject


async def test_forgot_password_is_rate_limited(api, db) -> None:
    client = api()
    await sign_up(client)
    for _ in range(3):
        assert (
            await api().post("/api/auth/forgot-password", json={"email": "trader@example.com"})
        ).status_code == 202
    resp = await api().post("/api/auth/forgot-password", json={"email": "trader@example.com"})
    assert resp.status_code == 429


async def test_changing_password_sends_a_notice(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    await deliver(db)
    resp = await client.post(
        "/api/account/password",
        json={
            "current_password": TEST_PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    [notice] = (await deliver(db)).sent
    assert "password was changed" in notice.subject


# --------------------------------------------------------------------------- outbox


async def test_outbox_retries_then_gives_up(api, db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_max_attempts", 2)
    await sign_up(api())
    await send_pending(db, redis=redis_client, mailer=FakeMailer(fail=MailError("timeout")))
    async with db() as session:
        [row] = list(await session.scalars(select(EmailOutbox)))
    assert row.status == "pending" and row.attempts == 1 and row.next_attempt_at > datetime.now(UTC)

    # Not due yet: nothing is tried.
    mailer = FakeMailer(fail=MailError("timeout"))
    await send_pending(db, redis=redis_client, mailer=mailer)
    async with db() as session, session.begin():
        await session.execute(update(EmailOutbox).values(next_attempt_at=datetime.now(UTC)))
    await send_pending(db, redis=redis_client, mailer=mailer)
    async with db() as session:
        [row] = list(await session.scalars(select(EmailOutbox)))
    assert row.status == "failed" and row.last_error == "timeout"


async def test_rejected_address_is_not_retried(api, db) -> None:
    await sign_up(api())
    await send_pending(
        db, redis=redis_client, mailer=FakeMailer(fail=MailError("refused", permanent=True))
    )
    async with db() as session:
        [row] = list(await session.scalars(select(EmailOutbox)))
    assert row.status == "failed" and row.attempts == 1


def test_message_has_text_and_html(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "email_from", "News Intelligence <alerts@newsintel.in>")
    monkeypatch.setattr(get_settings(), "email_reply_to", "support@newsintel.in")
    msg = build_message(OutgoingEmail("a@b.com", "Hello", "plain", "<p>html</p>"))
    assert msg["From"] == "News Intelligence <alerts@newsintel.in>"
    assert msg["Reply-To"] == "support@newsintel.in"
    assert msg["Message-ID"].endswith("@newsintel.in>")
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]


# --------------------------------------------------------------------------- billing emails


async def test_trial_reminder_once_per_trial(api, db) -> None:
    await sign_up(api())
    await deliver(db)
    assert await queue_trial_reminders(db) == 0  # 7 days left: too early
    async with db() as session, session.begin():
        await session.execute(
            update(User).values(trial_ends_at=datetime.now(UTC) + timedelta(hours=30))
        )
    assert await queue_trial_reminders(db) == 1
    assert await queue_trial_reminders(db) == 0  # already sent for this trial
    [mail] = (await deliver(db)).sent
    assert "trial ends tomorrow" in mail.subject and "/billing" in mail.text


async def test_checkout_needs_a_verified_email(api, db, monkeypatch) -> None:
    s = get_settings()
    for name, value in {"razorpay_key_id": "rzp_test_abc", "razorpay_key_secret": "x",
                        "razorpay_plan_pro": "plan_PRO"}.items():  # fmt: skip
        monkeypatch.setattr(s, name, value)
    client = api()
    headers = await sign_up(client)
    resp = await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=headers)
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "email_unverified"


async def test_failed_renewal_emails_once(api, db, rzp) -> None:  # noqa: F811
    from tests.test_billing import buy, event, signed

    client = api()
    headers = await sign_up(client)
    await buy(client, headers, rzp, "pro")
    await deliver(db)
    sub = next(iter(rzp.subs.values()))
    sub.update(status="pending")
    for _ in range(2):  # Razorpay may send it again
        raw, h = signed(event("subscription.pending", sub))
        await client.post("/api/billing/webhook", content=raw, headers=h)
    [mail] = (await deliver(db)).sent
    assert "renewal didn't go through" in mail.subject
