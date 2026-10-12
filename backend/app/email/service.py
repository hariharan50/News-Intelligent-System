"""Email outbox, single-use link tokens, and the account emails built on them."""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.email import templates
from app.email.sender import Mailer, MailError, OutgoingEmail, get_mailer
from app.models import EmailOutbox, EmailToken, User

log = logging.getLogger(__name__)

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"
SEND_LOCK = "nis:lock:email-send"


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def link(path: str, token: str | None = None) -> str:
    base = get_settings().app_base_url.rstrip("/")
    return f"{base}{path}" + (f"?token={token}" if token else "")


# --------------------------------------------------------------------------- outbox


async def enqueue(
    db: AsyncSession,
    *,
    to: str,
    template: str,
    rendered: templates.Rendered,
    user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Queue an email in the caller's transaction. False if this dedupe key was already used."""
    stmt = pg_insert(EmailOutbox).values(
        user_id=user_id, to_address=to, template=template, subject=rendered.subject,
        text_body=rendered.text, html_body=rendered.html, dedupe_key=dedupe_key,
    )  # fmt: skip
    if dedupe_key:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[EmailOutbox.dedupe_key],
            index_where=EmailOutbox.dedupe_key.is_not(None),
        )
    result = await db.execute(stmt.returning(EmailOutbox.id))
    return result.scalar() is not None


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** (attempts - 1)))  # 30 s, 1 min, 2 min ... 1 h


async def send_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: Redis,
    mailer: Mailer | None = None,
    limit: int = 50,
) -> int:
    """Send due emails, oldest first. One sender at a time. Returns how many were sent."""
    if not await redis.set(SEND_LOCK, "1", nx=True, ex=300):
        return 0
    mailer = mailer or get_mailer(redis)
    max_attempts = get_settings().email_max_attempts
    sent = 0
    try:
        async with session_factory() as db:
            due = list(
                await db.scalars(
                    select(EmailOutbox)
                    .where(
                        EmailOutbox.status == "pending",
                        EmailOutbox.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(EmailOutbox.created_at)
                    .limit(limit)
                )
            )
            for row in due:
                row.attempts += 1
                try:
                    await mailer.send(
                        OutgoingEmail(row.to_address, row.subject, row.text_body, row.html_body)
                    )
                except MailError as exc:
                    row.last_error = str(exc)
                    if exc.permanent or row.attempts >= max_attempts:
                        row.status = "failed"
                        log.warning(
                            "email %s to %s failed for good: %s", row.id, row.to_address, exc
                        )
                    else:
                        row.next_attempt_at = datetime.now(UTC) + _backoff(row.attempts)
                        log.info("email %s will be retried: %s", row.id, exc)
                else:
                    row.status = "sent"
                    row.sent_at = datetime.now(UTC)
                    row.last_error = None
                    sent += 1
                await db.commit()  # per email: a crash never re-sends what already went out
    finally:
        await redis.delete(SEND_LOCK)
    return sent


# --------------------------------------------------------------------------- tokens


async def issue_token(db: AsyncSession, user: User, purpose: str, ttl: timedelta) -> str:
    """A new single-use token; earlier unused tokens for the same purpose stop working."""
    now = datetime.now(UTC)
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        EmailToken(
            user_id=user.id, purpose=purpose, token_hash=_hash(token), email=str(user.email),
            expires_at=now + ttl,
        )
    )  # fmt: skip
    await db.flush()
    return token


async def consume_token(
    db: AsyncSession, token: str, purpose: str
) -> tuple[User, EmailToken] | None:
    """The token's user, marking the token used. None if unknown, used, expired or the
    account's email has changed since it was issued."""
    row = await db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == _hash(token), EmailToken.purpose == purpose)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    user = await db.get(User, row.user_id, with_for_update=True)
    if user is None or not user.is_active or str(user.email).lower() != row.email.lower():
        return None
    row.used_at = now
    return user, row


# --------------------------------------------------------------------------- account emails


async def send_verification(db: AsyncSession, user: User) -> None:
    hours = get_settings().email_verify_ttl_hours
    token = await issue_token(db, user, VERIFY_EMAIL, timedelta(hours=hours))
    rendered = templates.verify_email(user.full_name, link("/verify-email", token), hours)
    await enqueue(db, to=str(user.email), template=VERIFY_EMAIL, rendered=rendered, user_id=user.id)


async def send_password_reset(db: AsyncSession, user: User) -> None:
    minutes = get_settings().password_reset_ttl_minutes
    token = await issue_token(db, user, RESET_PASSWORD, timedelta(minutes=minutes))
    rendered = templates.reset_password(user.full_name, link("/reset-password", token), minutes)
    await enqueue(
        db, to=str(user.email), template=RESET_PASSWORD, rendered=rendered, user_id=user.id
    )


async def notify_password_changed(db: AsyncSession, user: User) -> None:
    rendered = templates.password_changed(user.full_name, link("/forgot-password"))
    await enqueue(
        db, to=str(user.email), template="password_changed", rendered=rendered, user_id=user.id
    )


async def notify_payment_failed(
    db: AsyncSession, user: User, plan_name: str, occasion: str
) -> None:
    rendered = templates.payment_failed(user.full_name, plan_name, link("/billing"))
    await enqueue(
        db, to=str(user.email), template="payment_failed", rendered=rendered, user_id=user.id,
        dedupe_key=f"payment_failed:{occasion}",
    )  # fmt: skip


async def queue_trial_reminders(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Once per trial: tell people whose trial ends within 2 days and who haven't subscribed."""
    from app.billing.plans import all_plans, entitlements_for

    s = get_settings()
    if not s.feature_billing:
        return 0
    now = datetime.now(UTC)
    queued = 0
    async with session_factory() as db:
        users = list(
            await db.scalars(
                select(User).where(
                    User.is_active,
                    User.trial_ends_at > now,
                    User.trial_ends_at <= now + timedelta(days=2),
                )
            )
        )
        if not users:
            return 0
        plans = await entitlements_for(db, [u.id for u in users], now)
        names = {code: p.name for code, p in (await all_plans(db)).items()}
        for user in users:
            if plans[user.id].source != "trial":  # already subscribed
                continue
            days = max(1, round((user.trial_ends_at - now) / timedelta(days=1)))
            rendered = templates.trial_ending(
                user.full_name, days, names.get(s.trial_plan, "Pro"), link("/billing")
            )
            queued += await enqueue(
                db, to=str(user.email), template="trial_ending", rendered=rendered,
                user_id=user.id, dedupe_key=f"trial_ending:{user.id}:{user.trial_ends_at:%Y%m%d}",
            )  # fmt: skip
        await db.commit()
    return queued
