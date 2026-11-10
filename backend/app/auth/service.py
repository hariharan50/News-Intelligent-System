"""Accounts and sessions: register, log in, refresh, revoke.

JWT + session design:
  * Access token: a JWT valid for 15 minutes, carrying the user id and session id (`sid`).
  * Session: one row per signed-in device. Every authenticated request checks that its
    session is still active (cached in Redis), so logout and revocation take effect
    immediately instead of when the JWT expires.
  * Refresh token: random, in an httpOnly cookie, stored only as a hash. Each refresh
    rotates it. Presenting an already-rotated token outside a short grace window means it
    was copied: the whole session is revoked.
  * Persistence: by default the cookie dies with the browser, so reopening it asks for the
    password. "Keep me signed in" sets UserSession.persistent, and only then does the
    cookie get a max-age. Either way a session nobody has refreshed for
    refresh_idle_timeout_hours can no longer be refreshed.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    burn_password_check,
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.auth.validation import TERMS_VERSION
from app.core.config import get_settings
from app.models import User, UserSession

log = logging.getLogger(__name__)

SESSION_CACHE_TTL_SECONDS = 300
# Written in place of deleting the cache key when a session is revoked. Deleting leaves a
# window: the revoking transaction has not committed yet, so a concurrent request reads the
# still-active row and caches "active" for the full TTL, and the revocation does nothing for
# five minutes. A tombstone can never be read as active, and positive entries are only ever
# written with NX, so a racing reader cannot overwrite it.
_REVOKED = "revoked"


def _session_cache_key(session_id: uuid.UUID) -> str:
    return f"nis:session:{session_id}"


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


INVALID_CREDENTIALS = AuthError(401, "invalid_credentials", "Email or password is incorrect.")
INVALID_REFRESH = AuthError(401, "invalid_refresh_token", "Please sign in again.")


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    session_id: uuid.UUID
    # None when an access token was re-issued without rotating (grace window).
    refresh_token: str | None
    # True only if the login asked to be kept signed in; decides whether the cookie gets a
    # max-age or dies with the browser. Carried through every rotation.
    persistent: bool = False


@dataclass(frozen=True, slots=True)
class ClientInfo:
    user_agent: str | None = None
    ip_address: str | None = None


async def _open_session(
    db: AsyncSession, user: User, client: ClientInfo, *, persistent: bool = False
) -> IssuedTokens:
    settings = get_settings()
    now = datetime.now(UTC)
    refresh = new_refresh_token()
    persistent = persistent and settings.allow_persistent_sessions
    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(refresh),
        user_agent=(client.user_agent or "")[:300] or None,
        ip_address=client.ip_address,
        expires_at=now + timedelta(days=settings.session_ttl_days),
        persistent=persistent,
    )
    db.add(row)
    await db.flush()
    access, ttl = create_access_token(user.id, row.id, now)
    return IssuedTokens(access, ttl, row.id, refresh, persistent=persistent)


@dataclass(frozen=True, slots=True)
class Signup:
    """Already validated by the API layer (name cleaned, phone in E.164, terms accepted)."""

    email: str
    password: str
    full_name: str
    phone: str
    marketing_opt_in: bool = False


EMAIL_TAKEN = AuthError(409, "email_taken", "An account with this email already exists.")
PHONE_TAKEN = AuthError(409, "phone_taken", "An account with this phone number already exists.")


async def register(
    db: AsyncSession, signup: Signup, *, client: ClientInfo
) -> tuple[User, IssuedTokens]:
    email = signup.email.strip()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise EMAIL_TAKEN
    if await db.scalar(select(User.id).where(User.phone == signup.phone)):
        raise PHONE_TAKEN
    now = datetime.now(UTC)
    user = User(
        email=email,
        password_hash=hash_password(signup.password),
        full_name=signup.full_name,
        phone=signup.phone,
        terms_version=TERMS_VERSION,
        terms_accepted_at=now,
        marketing_opt_in=signup.marketing_opt_in,
        password_changed_at=now,
        last_login_at=now,
        trial_ends_at=now + timedelta(days=get_settings().trial_days)
        if get_settings().trial_days > 0
        else None,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:  # a concurrent sign-up won the race
        raise PHONE_TAKEN if "phone" in str(exc.orig) else EMAIL_TAKEN from None
    return user, await _open_session(db, user, client)


async def change_password(
    db: AsyncSession,
    redis: Redis,
    *,
    user_id: uuid.UUID,
    current_session: uuid.UUID,
    current_password: str,
    new_password: str,
) -> int:
    """Change the password and sign out every other device. Returns sessions ended."""
    user = await db.get(User, user_id, with_for_update=True)
    if user is None or not verify_password(user.password_hash, current_password):
        raise AuthError(400, "wrong_password", "Your current password is incorrect.")
    if verify_password(user.password_hash, new_password):
        raise AuthError(422, "same_password", "Choose a password you haven't used here before.")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    return await revoke_all_sessions(
        db, redis, user_id, reason="password_changed", keep=current_session
    )


async def delete_account(
    db: AsyncSession, redis: Redis, *, user_id: uuid.UUID, password: str
) -> None:
    """Permanently delete the account and everything that belongs to it."""
    user = await db.get(User, user_id, with_for_update=True)
    if user is None or not verify_password(user.password_hash, password):
        raise AuthError(400, "wrong_password", "Your password is incorrect.")
    await revoke_all_sessions(db, redis, user_id, reason="account_deleted")
    await db.delete(user)  # profiles, scores, alerts, sessions... cascade in the database


async def login(
    db: AsyncSession, *, email: str, password: str, client: ClientInfo, remember: bool = False
) -> tuple[User, IssuedTokens | None]:
    """Check the password. With two-step sign-in on, no session is opened yet: the caller
    asks for the code and then calls finish_login(). Returns (user, tokens or None)."""
    user = await db.scalar(select(User).where(User.email == email.strip()))
    if user is None:
        burn_password_check(password)
        raise INVALID_CREDENTIALS
    if not verify_password(user.password_hash, password):
        raise INVALID_CREDENTIALS
    if not user.is_active:
        raise AuthError(403, "account_disabled", "This account has been disabled.")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if user.totp_enabled_at is not None:
        return user, None
    return user, await finish_login(db, user, client, remember=remember)


async def finish_login(
    db: AsyncSession, user: User, client: ClientInfo, *, remember: bool = False
) -> IssuedTokens:
    user.last_login_at = datetime.now(UTC)
    return await _open_session(db, user, client, persistent=remember)


async def refresh(
    db: AsyncSession, redis: Redis, *, refresh_token: str | None, client: ClientInfo
) -> IssuedTokens:
    if not refresh_token:
        raise INVALID_REFRESH
    settings = get_settings()
    now = datetime.now(UTC)
    presented = hash_token(refresh_token)

    idle_cutoff = now - timedelta(hours=settings.refresh_idle_timeout_hours)

    current = await db.scalar(
        select(UserSession).where(UserSession.token_hash == presented).with_for_update()
    )
    if current is not None:
        if current.revoked_at is not None or current.expires_at <= now:
            raise INVALID_REFRESH
        if current.last_used_at <= idle_cutoff:
            # Nobody has used this session for hours. A cookie left behind in a browser must
            # not silently sign someone back in: end it and ask for the password.
            await revoke_session(db, redis, current.id, reason="idle_timeout")
            raise INVALID_REFRESH
        if not await _user_is_active(db, current.user_id):
            raise INVALID_REFRESH
        rotated = new_refresh_token()
        current.previous_token_hash = presented
        current.token_hash = hash_token(rotated)
        current.rotated_at = now
        current.last_used_at = now
        if client.ip_address:
            current.ip_address = client.ip_address
        access, ttl = create_access_token(current.user_id, current.id, now)
        return IssuedTokens(access, ttl, current.id, rotated, persistent=current.persistent)

    previous = await db.scalar(
        select(UserSession).where(UserSession.previous_token_hash == presented).with_for_update()
    )
    if previous is None or previous.revoked_at is not None or previous.expires_at <= now:
        raise INVALID_REFRESH
    if previous.last_used_at <= idle_cutoff:
        await revoke_session(db, redis, previous.id, reason="idle_timeout")
        raise INVALID_REFRESH

    grace = timedelta(seconds=settings.refresh_reuse_grace_seconds)
    if previous.rotated_at is not None and now - previous.rotated_at <= grace:
        # Two tabs refreshed at once; the browser already holds the newer cookie.
        previous.last_used_at = now
        access, ttl = create_access_token(previous.user_id, previous.id, now)
        return IssuedTokens(access, ttl, previous.id, None, persistent=previous.persistent)

    log.warning("refresh token reuse detected; revoking session %s", previous.id)
    await revoke_session(db, redis, previous.id, reason="refresh_token_reuse")
    raise INVALID_REFRESH


async def _user_is_active(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return bool(await db.scalar(select(User.is_active).where(User.id == user_id)))


async def revoke_session(
    db: AsyncSession, redis: Redis, session_id: uuid.UUID, *, reason: str
) -> bool:
    result = await db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
    )
    # A tombstone, not a delete, and safe to write before the caller commits: see _REVOKED.
    await redis.set(_session_cache_key(session_id), _REVOKED, ex=SESSION_CACHE_TTL_SECONDS)
    return result.rowcount > 0


async def revoke_all_sessions(
    db: AsyncSession,
    redis: Redis,
    user_id: uuid.UUID,
    *,
    reason: str,
    keep: uuid.UUID | None = None,
) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoke_reason=reason)
        .returning(UserSession.id)
    )
    if keep is not None:
        stmt = stmt.where(UserSession.id != keep)
    revoked = list((await db.execute(stmt)).scalars())
    if revoked:
        pipe = redis.pipeline()
        for session_id in revoked:
            pipe.set(_session_cache_key(session_id), _REVOKED, ex=SESSION_CACHE_TTL_SECONDS)
        await pipe.execute()
    return len(revoked)


async def active_sessions(db: AsyncSession, user_id: uuid.UUID) -> list[UserSession]:
    now = datetime.now(UTC)
    result = await db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.last_used_at.desc())
    )
    return list(result)


async def session_is_active(
    db: AsyncSession, redis: Redis, session_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """The per-request "session" half of auth. Cached; revocation tombstones the key."""
    key = _session_cache_key(session_id)
    cached = await redis.get(key)
    if cached is not None:
        return cached == str(user_id)

    now = datetime.now(UTC)
    found = await db.scalar(
        select(UserSession.id)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            User.is_active.is_(True),
        )
    )
    if found is None:
        return False
    # NX: never overwrite a tombstone from a revocation that has not committed yet.
    await redis.set(key, str(user_id), ex=SESSION_CACHE_TTL_SECONDS, nx=True)
    return True


async def login_attempt_allowed(redis: Redis, email: str, ip: str | None) -> bool:
    """Count attempts per email+IP; refuse once over the limit for the rest of the window."""
    settings = get_settings()
    key = f"nis:login_attempts:{ip or '-'}:{email.strip().lower()}"
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, settings.login_window_seconds)
    return attempts <= settings.login_max_attempts


async def within_rate(redis: Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Count one more use of `key`; False once more than `limit` in the window."""
    full = f"nis:rate:{key}"
    used = await redis.incr(full)
    if used == 1:
        await redis.expire(full, window_seconds)
    return used <= limit


async def reset_password(db: AsyncSession, redis: Redis, user: User, new_password: str) -> int:
    """Set a new password from a reset link: every device is signed out. The link also
    proves the person controls the email address."""
    now = datetime.now(UTC)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = now
    user.has_password = True
    if user.email_verified_at is None:
        user.email_verified_at = now
    return await revoke_all_sessions(db, redis, user.id, reason="password_reset")


async def clear_login_attempts(redis: Redis, email: str, ip: str | None) -> None:
    await redis.delete(f"nis:login_attempts:{ip or '-'}:{email.strip().lower()}")
