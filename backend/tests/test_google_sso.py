"""Sign in with Google: the full OpenID Connect flow against a fake Google that signs real
RS256 ID tokens, and every check that keeps it safe (needs Docker PostgreSQL/Redis)."""

import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select, update

from app.api import auth as auth_api
from app.auth import google, totp
from app.auth.google import GoogleAuthError, safe_next
from app.core.config import get_settings
from app.models import Profile, User, UserIdentity
from tests.conftest import TEST_PASSWORD
from tests.test_profiles_feed_api import sign_up

pytestmark = pytest.mark.usefixtures("db")

CLIENT_ID = "test-client.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeGoogle:
    """Stands in for GoogleClient: returns a signed ID token for the code, like Google."""

    def __init__(self) -> None:
        self.claims: dict = {}
        self.sign_with = KEY
        self.exchanged: list[tuple[str, str]] = []

    async def exchange(self, code: str, verifier: str) -> str:
        self.exchanged.append((code, verifier))
        return jwt.encode(self.claims, self.sign_with, algorithm="RS256", headers={"kid": "k1"})

    def signing_key(self, id_token: str):  # noqa: ANN201
        return KEY.public_key()


@pytest.fixture
def fake_google(monkeypatch) -> FakeGoogle:
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(s, "google_client_secret", "test-secret")
    fake = FakeGoogle()
    monkeypatch.setattr(auth_api, "google_client", lambda: fake)
    return fake


def claims(expected_nonce: str, **overrides) -> dict:
    now = int(time.time())
    return {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "google-sub-123",
        "email": "asha@gmail.com", "email_verified": True, "name": "Asha Rao",
        "iat": now, "exp": now + 3600, "nonce": expected_nonce,
    } | overrides  # fmt: skip


async def start(client, **params) -> tuple[str, str, str]:
    """Begin the flow. Returns (state, nonce, Google URL)."""
    resp = await client.get("/api/auth/google/start", params=params)
    assert resp.status_code == 303, resp.text
    url = resp.headers["location"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    q = parse_qs(urlparse(url).query)
    return q["state"][0], q["nonce"][0], url


async def finish(client, fake: FakeGoogle, state: str, flow_nonce: str, **claim_overrides):
    fake.claims = claims(flow_nonce, **claim_overrides)
    return await client.get(
        "/api/auth/google/callback", params={"code": "auth-code", "state": state}
    )


async def signed_in_as(client) -> dict | None:
    resp = await client.post("/api/auth/refresh")
    if resp.status_code != 200:
        return None
    token = resp.json()["access_token"]
    return (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})).json()


# --------------------------------------------------------------------------- the request to Google


async def test_start_sends_a_pkce_request_and_binds_the_browser(api, fake_google) -> None:
    client = api()
    state, nonce, url = await start(client, next="/alerts", consent="true")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == ["http://127.0.0.1:8081/api/auth/google/callback"]
    assert q["response_type"] == ["code"] and q["scope"] == ["openid email profile"]
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
    assert len(state) >= 40 and len(nonce) >= 30
    assert client.cookies.get(google.STATE_COOKIE) == state


async def test_not_configured(api) -> None:
    resp = await api().get("/api/auth/google/start")
    assert resp.status_code == 303 and resp.headers["location"] == "/login?sso_error=not_configured"


def test_next_must_stay_on_our_site() -> None:
    assert safe_next("/alerts?x=1") == "/alerts?x=1"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "", None, "javascript:alert(1)"):
        assert safe_next(bad) == "/feed"


# --------------------------------------------------------------------------- new accounts


async def test_new_account_with_consent(api, db, fake_google) -> None:
    client = api()
    state, nonce, _ = await start(client, consent="true")
    resp = await finish(client, fake_google, state, nonce)
    assert resp.status_code == 303 and resp.headers["location"] == "/feed?welcome=1"
    assert fake_google.exchanged[0][0] == "auth-code" and len(fake_google.exchanged[0][1]) > 40
    assert "nis_refresh" in resp.headers.get("set-cookie", "")

    me = await signed_in_as(client)
    assert me["email"] == "asha@gmail.com" and me["full_name"] == "Asha Rao"
    assert me["email_verified"] is True and me["has_password"] is False
    async with db() as session:
        user = await session.scalar(select(User).where(User.email == "asha@gmail.com"))
        identity = await session.scalar(select(UserIdentity))
        profiles = list(await session.scalars(select(Profile).where(Profile.user_id == user.id)))
    assert identity.subject == "google-sub-123" and identity.user_id == user.id
    assert user.terms_accepted_at is not None and len(profiles) == 1
    # The random password can't be used to sign in.
    login = await api().post(
        "/api/auth/login", json={"email": "asha@gmail.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401

    # Next time: matched by Google's subject id, even if the Gmail address changed.
    again = api()
    state, nonce, _ = await start(again)
    resp = await finish(again, fake_google, state, nonce, email="asha.rao@gmail.com")
    assert resp.headers["location"] == "/feed"
    assert (await signed_in_as(again))["id"] == me["id"]


async def test_new_account_needs_consent_first(api, db, fake_google) -> None:
    client = api()
    state, nonce, _ = await start(client)  # from the sign-in page: no consent given
    resp = await finish(client, fake_google, state, nonce)
    assert resp.headers["location"] == "/signup?sso=needs_consent"
    async with db() as session:
        assert await session.scalar(select(User).where(User.email == "asha@gmail.com")) is None


# --------------------------------------------------------------------------- existing accounts


async def test_links_to_a_confirmed_account_and_keeps_its_password(api, db, fake_google) -> None:
    owner = api()
    headers = await sign_up(owner, "asha@gmail.com")
    async with db() as session, session.begin():
        await session.execute(update(User).values(email_verified_at=User.created_at))
    client = api()
    state, nonce, _ = await start(client)
    resp = await finish(client, fake_google, state, nonce)
    assert resp.headers["location"] == "/feed"
    me = await signed_in_as(client)
    assert me["email"] == "asha@gmail.com" and me["has_password"] is True
    # Nothing was taken from the existing owner.
    assert (await owner.get("/api/auth/me", headers=headers)).status_code == 200
    login = await api().post(
        "/api/auth/login", json={"email": "asha@gmail.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    methods = (await owner.get("/api/account/sign-in-methods", headers=headers)).json()
    assert methods["password"] is True and methods["google"]["email"] == "asha@gmail.com"


async def test_unconfirmed_account_is_reclaimed_by_the_real_owner(api, db, fake_google) -> None:
    """Someone registered the address without confirming it (pre-hijacking). Google proves
    who owns it: that person's password, 2FA and sessions are removed."""
    squatter = api()
    headers = await sign_up(squatter, "asha@gmail.com")
    setup = (await squatter.post("/api/account/2fa/setup", headers=headers)).json()
    await squatter.post(
        "/api/account/2fa/enable",
        json={"code": totp.current_code(setup["secret"])},
        headers=headers,
    )

    client = api()
    state, nonce, _ = await start(client)
    resp = await finish(client, fake_google, state, nonce)
    assert resp.headers["location"] == "/feed"  # no 2FA prompt: the squatter's 2FA is gone
    me = await signed_in_as(client)
    assert me["email_verified"] is True and me["has_password"] is False
    assert me["two_factor_enabled"] is False
    assert (await squatter.get("/api/auth/me", headers=headers)).status_code == 401
    login = await api().post(
        "/api/auth/login", json={"email": "asha@gmail.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401


async def test_two_step_sign_in_still_applies(api, db, fake_google) -> None:
    owner = api()
    headers = await sign_up(owner, "asha@gmail.com")
    async with db() as session, session.begin():
        await session.execute(update(User).values(email_verified_at=User.created_at))
    setup = (await owner.post("/api/account/2fa/setup", headers=headers)).json()
    await owner.post(
        "/api/account/2fa/enable",
        json={"code": totp.current_code(setup["secret"])},
        headers=headers,
    )

    client = api()
    state, nonce, _ = await start(client, next="/alerts")
    resp = await finish(client, fake_google, state, nonce)
    location = resp.headers["location"]
    assert location.startswith("/login?next=/alerts#mfa=")
    assert "nis_refresh" not in resp.headers.get("set-cookie", "")  # no session yet
    mfa_token = location.split("#mfa=", 1)[1]
    code = totp.current_code(setup["secret"], at=time.time() + 30)
    ok = await client.post("/api/auth/login/2fa", json={"mfa_token": mfa_token, "code": code})
    assert ok.status_code == 200


async def test_disabled_account_is_refused(api, db, fake_google) -> None:
    await sign_up(api(), "asha@gmail.com")
    async with db() as session, session.begin():
        await session.execute(
            update(User).values(is_active=False, email_verified_at=User.created_at)
        )
    client = api()
    state, nonce, _ = await start(client)
    resp = await finish(client, fake_google, state, nonce)
    assert resp.headers["location"] == "/login?sso_error=account_disabled"


# --------------------------------------------------------------------------- attacks


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"aud": "someone-else.apps.googleusercontent.com"}, "bad_token"),
        ({"iss": "https://evil.example.com"}, "bad_token"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "bad_token"),
        ({"nonce": "not-the-nonce"}, "bad_token"),
        ({"email_verified": False}, "email_unverified"),
        ({"email": ""}, "no_email"),
    ],
)
async def test_bad_id_tokens_are_refused(api, db, fake_google, overrides, error) -> None:
    client = api()
    state, nonce, _ = await start(client, consent="true")
    resp = await finish(client, fake_google, state, nonce, **overrides)
    assert resp.headers["location"] == f"/login?sso_error={error}"
    assert await signed_in_as(client) is None
    async with db() as session:
        assert list(await session.scalars(select(User))) == []


async def test_token_signed_by_another_key_is_refused(api, fake_google) -> None:
    client = api()
    state, nonce, _ = await start(client, consent="true")
    fake_google.sign_with = OTHER_KEY
    resp = await finish(client, fake_google, state, nonce)
    assert resp.headers["location"] == "/login?sso_error=bad_token"


async def test_state_must_match_this_browser_and_work_once(api, fake_google) -> None:
    victim = api()
    state, nonce, _ = await start(victim, consent="true")

    # Another browser (an attacker's link) can't complete someone else's flow...
    attacker = api()
    resp = await finish(attacker, fake_google, state, nonce)
    assert resp.headers["location"] == "/login?sso_error=state_mismatch"

    # ...the right browser can, once...
    assert (await finish(victim, fake_google, state, nonce)).headers[
        "location"
    ] == "/feed?welcome=1"
    # ...and replaying the same callback fails.
    victim.cookies.set(google.STATE_COOKIE, state, path="/api/auth/google")
    replay = await finish(victim, fake_google, state, nonce)
    assert replay.headers["location"] == "/login?sso_error=expired"


async def test_cancel_on_googles_screen(api, fake_google) -> None:
    client = api()
    state, _, _ = await start(client)
    resp = await client.get(
        "/api/auth/google/callback", params={"state": state, "error": "access_denied"}
    )
    assert resp.headers["location"] == "/login?sso_error=cancelled"


async def test_disconnecting_google_needs_a_password(api, fake_google) -> None:
    client = api()
    state, nonce, _ = await start(client, consent="true")
    await finish(client, fake_google, state, nonce)
    token = (await client.post("/api/auth/refresh")).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.delete("/api/account/sign-in-methods/google", headers=headers)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "no_password"


def test_verify_rejects_none_algorithm() -> None:
    fake = FakeGoogle()
    unsigned = jwt.encode(claims("n"), key=None, algorithm="none")
    with pytest.raises(GoogleAuthError):
        google.verify_id_token(fake, unsigned, "n")
