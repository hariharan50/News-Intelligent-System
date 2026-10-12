"""Sign in with Google: OpenID Connect, authorization code flow with PKCE.

The browser only ever carries a one-time `code` and `state`. Our server exchanges the code
for Google's ID token (with the client secret and the PKCE verifier), verifies the token
itself, and then opens one of our own sessions. Google tokens are never stored or sent to
the browser.
"""

import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from redis.asyncio import Redis

from app.core.config import get_settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
FLOW_TTL = 600  # seconds to complete the Google screen
STATE_COOKIE = "nis_oauth_state"


class GoogleAuthError(Exception):
    """Any failure in the Google flow. `code` is safe to show; details only go to the log."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Flow:
    verifier: str
    nonce: str
    next: str
    consent: bool  # the person ticked Terms + risk notice (needed to create an account)


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str  # Google's stable user id ("sub"); emails can change, this can't
    email: str
    email_verified: bool
    name: str | None


def redirect_uri() -> str:
    s = get_settings()
    return s.google_redirect_uri or f"{s.app_base_url.rstrip('/')}/api/auth/google/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def safe_next(value: str | None) -> str:
    """Only paths on our own site: '/feed' yes, '//evil.com' and 'https://...' no."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:300]
    return "/feed"


async def start(redis: Redis, *, next_path: str | None, consent: bool) -> tuple[str, str]:
    """Begin a sign-in. Returns (Google URL to redirect to, state for the browser cookie)."""
    s = get_settings()
    state = secrets.token_urlsafe(32)
    flow = Flow(
        verifier=secrets.token_urlsafe(64),
        nonce=secrets.token_urlsafe(24),
        next=safe_next(next_path),
        consent=consent,
    )
    await redis.set(f"nis:oauth:{state}", json.dumps(asdict(flow)), ex=FLOW_TTL)
    challenge = _b64(hashlib.sha256(flow.verifier.encode()).digest())
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": flow.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
        "access_type": "online",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}", state


async def take_flow(redis: Redis, state: str | None, cookie_state: str | None) -> Flow:
    """The flow for this callback, once. The state must match the cookie set on this browser
    when the flow began (so a code obtained elsewhere can't be injected) and the Redis record
    (so it's ours, recent, and unused)."""
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise GoogleAuthError("state_mismatch")
    raw = await redis.getdel(f"nis:oauth:{state}")
    if raw is None:
        raise GoogleAuthError("expired")
    return Flow(**json.loads(raw))


class GoogleClient:
    """The two calls to Google. Tests replace this with a fake."""

    def __init__(
        self, http: httpx.AsyncClient | None = None, jwks: PyJWKClient | None = None
    ) -> None:
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self.jwks = jwks or _jwks_client()

    async def exchange(self, code: str, verifier: str) -> str:
        s = get_settings()
        try:
            resp = await self.http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": s.google_client_id,
                    "client_secret": s.google_client_secret,
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GoogleAuthError("google_unreachable", str(exc)) from None
        if resp.status_code != 200:
            raise GoogleAuthError("exchange_failed", resp.text[:300])
        id_token = resp.json().get("id_token")
        if not id_token:
            raise GoogleAuthError("exchange_failed", "no id_token in the response")
        return id_token

    def signing_key(self, id_token: str):  # noqa: ANN201
        try:
            return self.jwks.get_signing_key_from_jwt(id_token).key
        except jwt.PyJWTError as exc:
            raise GoogleAuthError("bad_token", f"signing key: {exc}") from None


def _jwks_client() -> PyJWKClient:
    # Google rotates its keys; PyJWKClient caches them and refetches for an unknown key id.
    return PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)


def verify_id_token(client: GoogleClient, id_token: str, nonce: str) -> GoogleIdentity:
    """Signature (Google's keys, RS256 only), issuer, audience, expiry, nonce, verified email."""
    s = get_settings()
    try:
        claims = jwt.decode(
            id_token,
            client.signing_key(id_token),
            algorithms=["RS256"],
            audience=s.google_client_id,
            issuer=ISSUERS,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            leeway=60,
        )
    except jwt.PyJWTError as exc:
        raise GoogleAuthError("bad_token", str(exc)) from None
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleAuthError("bad_token", "nonce mismatch")
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleAuthError("no_email")
    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=email,
        email_verified=claims.get("email_verified") is True,
        name=(str(claims.get("name")).strip()[:120] or None) if claims.get("name") else None,
    )
