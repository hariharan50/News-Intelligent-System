"""Which account a verified Google identity signs in to.

1. Already linked (same Google subject): that account.
2. An account with the same email: link Google to it. If that account never confirmed its
   email, someone else may have registered it to hijack the address later ("account
   pre-hijacking"). Google has just proven who owns the address, so the account is reset
   to them: its password, two-step sign-in and sessions are removed.
3. No account: create one, but only if the person accepted the Terms and the risk notice
   on our sign-up page before going to Google.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import Flow, GoogleAuthError, GoogleIdentity
from app.auth.security import hash_password
from app.auth.service import revoke_all_sessions
from app.auth.validation import TERMS_VERSION
from app.core.config import get_settings
from app.models import RecoveryCode, User, UserIdentity

log = logging.getLogger(__name__)
PROVIDER = "google"


def unusable_password() -> str:
    """A real Argon2 hash of a random secret nobody knows: password sign-in can't succeed,
    and the row looks like any other account's."""
    return hash_password(secrets.token_urlsafe(32))


async def resolve_google_user(
    db: AsyncSession, redis: Redis, identity: GoogleIdentity, flow: Flow
) -> tuple[User, bool]:
    """The account to sign in. Returns (user, created)."""
    if not identity.email_verified:
        raise GoogleAuthError("email_unverified")
    now = datetime.now(UTC)

    link = await db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == PROVIDER, UserIdentity.subject == identity.subject
        )
    )
    if link is not None:
        user = await db.get(User, link.user_id, with_for_update=True)
        link.email = identity.email
        link.last_used_at = now
        created = False
    else:
        user = await db.scalar(select(User).where(User.email == identity.email).with_for_update())
        created = user is None
        if user is None:
            if not flow.consent:
                raise GoogleAuthError("needs_consent")
            user = await _create(db, identity, now)
        else:
            if user.email_verified_at is None:
                await _reclaim(db, redis, user, now)
            # One Google account per user: an old link to another Google account is replaced.
            await db.execute(
                delete(UserIdentity).where(
                    UserIdentity.user_id == user.id, UserIdentity.provider == PROVIDER
                )
            )
        db.add(
            UserIdentity(
                user_id=user.id, provider=PROVIDER, subject=identity.subject,
                email=identity.email, last_used_at=now,
            )
        )  # fmt: skip
    if user is None or not user.is_active:
        raise GoogleAuthError("account_disabled")
    if user.email_verified_at is None:
        user.email_verified_at = now  # Google verified it
    await db.flush()
    return user, created


async def _create(db: AsyncSession, identity: GoogleIdentity, now: datetime) -> User:
    s = get_settings()
    user = User(
        email=identity.email,
        password_hash=unusable_password(),
        has_password=False,
        full_name=identity.name,
        email_verified_at=now,
        terms_version=TERMS_VERSION,
        terms_accepted_at=now,
        last_login_at=now,
        trial_ends_at=now + timedelta(days=s.trial_days) if s.trial_days > 0 else None,
    )
    db.add(user)
    await db.flush()
    log.info("account created with Google: %s", user.id)
    return user


async def _reclaim(db: AsyncSession, redis: Redis, user: User, now: datetime) -> None:
    """Hand an unconfirmed account to the person Google says owns the email."""
    user.password_hash = unusable_password()
    user.has_password = False
    user.password_changed_at = now
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.totp_last_counter = None
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await revoke_all_sessions(db, redis, user.id, reason="claimed_by_google")
    log.warning("unconfirmed account %s claimed via Google: credentials reset", user.id)
