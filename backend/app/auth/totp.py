"""Two-factor sign-in with authenticator apps (TOTP, RFC 6238) and one-time recovery codes.

Secrets are encrypted at rest (AES-GCM). Each accepted code's time step is remembered, so a
code can't be used twice, even within its 30-second window.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

ISSUER = "News Intelligence"
STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous and next step too (clock drift)
RECOVERY_CODES = 10


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret: str, account: str) -> str:
    query = urlencode(
        {"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?{query}"


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    return _code_at(secret, int((at or time.time()) // STEP_SECONDS))


def verify(
    secret: str, code: str, *, last_counter: int | None, at: float | None = None
) -> int | None:
    """The matched time step, or None. Steps at or before `last_counter` are refused (replay)."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((at or time.time()) // STEP_SECONDS)
    for counter in range(now - WINDOW, now + WINDOW + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(_code_at(secret, counter), code):
            return counter
    return None


# --------------------------------------------------------------------------- recovery codes


def new_recovery_codes() -> list[str]:
    """Ten codes like 'k7m2-q9xd', shown once."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return [
        "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
        for _ in range(RECOVERY_CODES)
    ]


def hash_recovery_code(code: str) -> str:
    normalized = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256(normalized.encode()).hexdigest()


# --------------------------------------------------------------------------- encryption at rest


def _key() -> bytes:
    s = get_settings()
    if s.data_encryption_key:
        return base64.urlsafe_b64decode(s.data_encryption_key)
    # Fallback for development: derived from the JWT secret (rotating it would break 2FA).
    return HKDF(algorithm=SHA256(), length=32, salt=None, info=b"nis-totp").derive(
        s.jwt_secret.encode()
    )


def encrypt(secret: str) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_key()).encrypt(nonce, secret.encode(), b"totp")
    return base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(token: str) -> str:
    raw = base64.urlsafe_b64decode(token)
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"totp").decode()
