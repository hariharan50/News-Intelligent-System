"""Two-factor sign-in: setting it up, the second login step, recovery codes."""

import secrets
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import totp
from app.models import RecoveryCode, User

CHALLENGE_TTL = 300  # seconds to type the code after the password step
CHALLENGE_ATTEMPTS = 5
SETUP_TTL = 600


class TwoFactorError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


WRONG_CODE = TwoFactorError(
    400, "wrong_code", "That code isn't right. Check your authenticator app."
)


# --------------------------------------------------------------------------- login challenge


async def start_challenge(redis: Redis, user_id: uuid.UUID, *, remember: bool = False) -> str:
    """After a correct password: a short-lived token that the code step must present.

    `remember` carries the login form's "Keep me signed in" across the two steps, so the
    code step does not have to be trusted to repeat it.
    """
    token = secrets.token_urlsafe(32)
    await redis.set(f"nis:mfa:{token}", str(user_id), ex=CHALLENGE_TTL)
    if remember:
        await redis.set(f"nis:mfa:{token}:remember", "1", ex=CHALLENGE_TTL)
    return token


async def challenge_remember(redis: Redis, token: str) -> bool:
    """Whether the password step asked to be kept signed in."""
    return await redis.get(f"nis:mfa:{token}:remember") == "1"


async def challenge_user(redis: Redis, token: str) -> uuid.UUID | None:
    """The user a challenge belongs to; None once expired or after too many wrong codes."""
    key = f"nis:mfa:{token}"
    user_id = await redis.get(key)
    if user_id is None:
        return None
    tries = await redis.incr(f"{key}:tries")
    await redis.expire(f"{key}:tries", CHALLENGE_TTL)
    if tries > CHALLENGE_ATTEMPTS:
        await redis.delete(key)
        return None
    return uuid.UUID(user_id)


async def end_challenge(redis: Redis, token: str) -> None:
    await redis.delete(f"nis:mfa:{token}", f"nis:mfa:{token}:tries", f"nis:mfa:{token}:remember")


async def check_code(db: AsyncSession, user: User, code: str) -> bool:
    """An authenticator code, or else an unused recovery code (used up on success)."""
    if not user.totp_secret_enc:
        return False
    counter = totp.verify(
        totp.decrypt(user.totp_secret_enc), code, last_counter=user.totp_last_counter
    )
    if counter is not None:
        user.totp_last_counter = counter
        return True
    used = await db.execute(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == totp.hash_recovery_code(code),
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(RecoveryCode.id)
    )
    return used.scalar() is not None


# --------------------------------------------------------------------------- setup


async def begin_setup(redis: Redis, user: User) -> dict:
    """A new secret, kept aside until the user proves their app shows the right code."""
    secret = totp.new_secret()
    await redis.set(f"nis:totp-setup:{user.id}", secret, ex=SETUP_TTL)
    return {"secret": secret, "otpauth_url": totp.provisioning_uri(secret, str(user.email))}


async def _replace_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = totp.new_recovery_codes()
    db.add_all(RecoveryCode(user_id=user_id, code_hash=totp.hash_recovery_code(c)) for c in codes)
    await db.flush()
    return codes


async def enable(db: AsyncSession, redis: Redis, user: User, code: str) -> list[str]:
    """Turn 2FA on with the first code from the app. Returns recovery codes (shown once)."""
    if user.totp_enabled_at is not None:
        raise TwoFactorError(409, "already_enabled", "Two-step sign-in is already on.")
    secret = await redis.get(f"nis:totp-setup:{user.id}")
    if secret is None:
        raise TwoFactorError(400, "setup_expired", "Setup took too long. Start again.")
    counter = totp.verify(secret, code, last_counter=None)
    if counter is None:
        raise WRONG_CODE
    user.totp_secret_enc = totp.encrypt(secret)
    user.totp_last_counter = counter
    user.totp_enabled_at = datetime.now(UTC)
    await redis.delete(f"nis:totp-setup:{user.id}")
    return await _replace_recovery_codes(db, user.id)


async def disable(db: AsyncSession, user: User, code: str) -> None:
    if user.totp_enabled_at is None:
        return
    if not await check_code(db, user, code):
        raise WRONG_CODE
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.totp_last_counter = None
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))


async def regenerate_codes(db: AsyncSession, user: User, code: str) -> list[str]:
    if user.totp_enabled_at is None:
        raise TwoFactorError(409, "not_enabled", "Turn on two-step sign-in first.")
    if not await check_code(db, user, code):
        raise WRONG_CODE
    return await _replace_recovery_codes(db, user.id)


async def codes_left(db: AsyncSession, user_id: uuid.UUID) -> int:
    return await db.scalar(
        select(func.count()).where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
    )
