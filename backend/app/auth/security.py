"""Password hashing, access tokens (JWT) and refresh tokens."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters
_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("timing-equaliser-not-a-real-password")


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check, so unknown emails can't be found by timing."""
    verify_password(_dummy_hash(), password)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


class InvalidTokenError(Exception):
    pass


def create_access_token(
    user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, int]:
    """Returns (token, lifetime in seconds)."""
    settings = get_settings()
    now = now or datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM), ttl


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[_ALGORITHM],  # never trust the token's own "alg" header
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "sid", "exp", "iat", "typ"]},
        )
        if payload["typ"] != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("wrong token type")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            session_id=uuid.UUID(payload["sid"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc
