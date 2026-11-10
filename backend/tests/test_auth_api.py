"""JWT + session auth, end to end through the HTTP API (needs Docker PostgreSQL/Redis)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models import Profile, User, UserSession
from tests.conftest import TEST_PASSWORD as PASSWORD
from tests.conftest import signup_body

pytestmark = pytest.mark.usefixtures("db")

COOKIE = get_settings().refresh_cookie_name


async def register(client: AsyncClient, email: str = "trader@example.com") -> dict:
    resp = await client.post("/api/auth/register", json=signup_body(email))
    assert resp.status_code == 201, resp.text
    return resp.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_register_signs_in_and_creates_trader_profile(api, db) -> None:
    client = api()
    resp = await client.post(
        "/api/auth/register",
        json=signup_body("Trader@Example.com", phone="098765 43210", marketing_opt_in=True),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"]["email"] == "Trader@example.com"  # domains are case-insensitive
    assert body["user"]["phone"] == "+91 98765 43210"  # normalised, shown formatted
    assert body["user"]["full_name"] == "Asha Rao"

    set_cookie = resp.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in set_cookie
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    assert "path=/api/auth" in set_cookie

    me = await client.get("/api/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200 and me.json()["marketing_opt_in"] is True

    async with db() as session:
        user = await session.scalar(select(User))
        profile = await session.scalar(select(Profile).where(Profile.user_id == user.id))
    assert user.password_hash.startswith("$argon2id$")
    assert user.phone == "+919876543210"  # stored in E.164
    assert user.terms_version and user.terms_accepted_at  # consent is recorded
    assert profile.template == "indian_markets_trader"


async def test_email_is_unique_case_insensitively(api) -> None:
    await register(api(), "trader@example.com")
    resp = await api().post("/api/auth/register", json=signup_body("TRADER@example.com"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "email_taken"


async def test_phone_is_unique_however_it_is_written(api) -> None:
    first = await api().post(
        "/api/auth/register", json=signup_body("a@example.com", phone="98765 43210")
    )
    assert first.status_code == 201
    again = await api().post(
        "/api/auth/register", json=signup_body("b@example.com", phone="+91-98765-43210")
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "phone_taken"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"full_name": " "}, "Enter your name"),
        ({"full_name": "http://spam.example"}, "links"),
        ({"phone": ""}, "phone number"),
        ({"phone": "12345"}, "isn't valid"),
        ({"phone": "98765abcde"}, "digits"),
        ({"password": "short1", "confirm_password": "short1"}, "at least 10"),
        ({"password": "onlyletters", "confirm_password": "onlyletters"}, "one number"),
        ({"password": "1234567890", "confirm_password": "1234567890"}, "one letter"),
        ({"password": "password1234", "confirm_password": "password1234"}, "too common"),
        ({"password": "asharao-2026!", "confirm_password": "asharao-2026!"}, "name or email"),
        ({"confirm_password": "something else 99"}, "don't match"),
        ({"accept_terms": False}, "Terms"),
        ({"accept_risk_notice": False}, "not investment advice"),
        ({"password": "x1" * 65, "confirm_password": "x1" * 65}, ""),
    ],
)
async def test_register_validation(api, overrides: dict, message: str) -> None:
    resp = await api().post("/api/auth/register", json=signup_body("v@example.com") | overrides)
    assert resp.status_code == 422, resp.text
    assert message.lower() in resp.text.lower()


async def test_register_requires_every_field(api) -> None:
    for field in ("full_name", "phone", "confirm_password", "accept_terms", "accept_risk_notice"):
        body = signup_body("missing@example.com")
        del body[field]
        assert (await api().post("/api/auth/register", json=body)).status_code == 422, field


async def test_login_success_and_failures_look_the_same(api) -> None:
    await register(api())
    ok = await api().post(
        "/api/auth/login", json={"email": "trader@example.com", "password": PASSWORD}
    )
    assert ok.status_code == 200 and ok.json()["access_token"]

    wrong = await api().post(
        "/api/auth/login", json={"email": "trader@example.com", "password": "wrong password"}
    )
    unknown = await api().post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrong password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_login_is_rate_limited(api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "login_max_attempts", 3)
    await register(api())
    client = api()
    bad = {"email": "trader@example.com", "password": "wrong password"}
    codes = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    # Even the right password is refused until the window passes.
    good = {"email": "trader@example.com", "password": PASSWORD}
    assert (await client.post("/api/auth/login", json=good)).status_code == 429


async def test_form_token_endpoint_for_api_docs(api) -> None:
    await register(api())
    resp = await api().post(
        "/api/auth/token", data={"username": "trader@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 200 and resp.json()["access_token"]


async def test_refresh_rotates_the_cookie(api) -> None:
    client = api()
    await register(client)
    first_cookie = client.cookies[COOKIE]

    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    assert client.cookies[COOKIE] != first_cookie
    me = await client.get("/api/auth/me", headers=bearer(resp.json()["access_token"]))
    assert me.status_code == 200


async def test_concurrent_refresh_within_grace_is_tolerated(api) -> None:
    client = api()
    await register(client)
    old = client.cookies[COOKIE]
    await client.post("/api/auth/refresh")

    resp = await api().post("/api/auth/refresh", headers={"Cookie": f"{COOKIE}={old}"})
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers  # the browser already has the newer cookie


async def test_reused_refresh_token_revokes_the_session(api, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "refresh_reuse_grace_seconds", 0)
    victim = api()
    tokens = await register(victim)
    stolen = victim.cookies[COOKIE]
    await victim.post("/api/auth/refresh")  # victim rotates; the stolen copy is now stale

    reuse = await api().post("/api/auth/refresh", headers={"Cookie": f"{COOKIE}={stolen}"})
    assert reuse.status_code == 401

    # The whole session is gone: the victim's access token and new cookie both stop working.
    assert (
        await victim.get("/api/auth/me", headers=bearer(tokens["access_token"]))
    ).status_code == 401
    assert (await victim.post("/api/auth/refresh")).status_code == 401


async def test_logout_revokes_access_token_immediately(api) -> None:
    client = api()
    tokens = await register(client)
    headers = bearer(tokens["access_token"])
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200  # cached

    resp = await client.post("/api/auth/logout", headers=headers)
    assert resp.status_code == 204
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_logout_all_and_device_list(api) -> None:
    phone, laptop = api(), api()
    await register(phone)
    login = await laptop.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": PASSWORD}
    )
    laptop_headers = bearer(login.json()["access_token"])

    devices = (await laptop.get("/api/auth/sessions", headers=laptop_headers)).json()
    assert len(devices) == 2
    assert [d["current"] for d in devices].count(True) == 1

    assert (await laptop.post("/api/auth/logout-all", headers=laptop_headers)).status_code == 204
    assert (await laptop.get("/api/auth/me", headers=laptop_headers)).status_code == 401
    assert (await phone.post("/api/auth/refresh")).status_code == 401


async def test_revoke_one_device_but_not_someone_elses(api) -> None:
    mine, other = api(), api()
    my_tokens = await register(mine, "me@example.com")
    other_tokens = await register(other, "other@example.com")
    other_session = (
        await other.get("/api/auth/sessions", headers=bearer(other_tokens["access_token"]))
    ).json()[0]["id"]

    resp = await mine.delete(
        f"/api/auth/sessions/{other_session}", headers=bearer(my_tokens["access_token"])
    )
    assert resp.status_code == 404
    still = await other.get("/api/auth/me", headers=bearer(other_tokens["access_token"]))
    assert still.status_code == 200


@pytest.mark.parametrize("variant", ["wrong_secret", "expired", "alg_none", "garbage"])
async def test_forged_or_expired_tokens_are_rejected(api, variant: str) -> None:
    client = api()
    tokens = await register(client)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    if variant == "wrong_secret":
        token = jwt.encode(claims, "x" * 40, algorithm="HS256")
    elif variant == "expired":
        past = datetime.now(UTC) - timedelta(hours=1)
        token = jwt.encode(
            claims | {"exp": int(past.timestamp())}, get_settings().jwt_secret, algorithm="HS256"
        )
    elif variant == "alg_none":
        token = jwt.encode(claims, None, algorithm="none")
    else:
        token = "not.a.jwt"
    assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 401


async def test_expired_session_is_rejected_even_with_valid_jwt(api, db) -> None:
    client = api()
    tokens = await register(client)
    async with db() as session, session.begin():
        await session.execute(
            update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    from app.cache.redis import redis_client

    await redis_client.flushdb()  # drop the cached "session active" entry
    assert (
        await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))
    ).status_code == 401


async def test_disabled_account_cannot_log_in(api, db) -> None:
    await register(api())
    async with db() as session, session.begin():
        await session.execute(update(User).values(is_active=False))
    resp = await api().post(
        "/api/auth/login", json={"email": "trader@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 403


async def test_protected_routes_require_auth(api) -> None:
    client = api()
    for method, path in [("get", "/api/auth/me"), ("get", "/api/feed"), ("get", "/api/profiles")]:
        assert (await getattr(client, method)(path)).status_code == 401


async def test_login_cookie_dies_with_the_browser_unless_remembered(api) -> None:
    """The default sign-in must not leave a cookie that survives closing the browser.

    This is the bug behind "clicking Login walked straight into the app": a persistent
    30-day cookie let any later visit silently mint a fresh access token.
    """
    plain = await api().post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert plain.status_code == 401  # no account yet; registered below

    client = api()
    await register(client)

    forgetful = await client.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": PASSWORD}
    )
    assert forgetful.status_code == 200
    cookie = forgetful.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie
    assert "max-age" not in cookie and "expires=" not in cookie  # a session cookie

    remembered = await api().post(
        "/api/auth/login",
        json={"email": "trader@example.com", "password": PASSWORD, "remember_me": True},
    )
    assert remembered.status_code == 200
    cookie = remembered.headers["set-cookie"].lower()
    assert f"max-age={get_settings().session_ttl_days * 86400}" in cookie


async def test_remembering_survives_rotation(api) -> None:
    client = api()
    await register(client)
    await client.post(
        "/api/auth/login",
        json={"email": "trader@example.com", "password": PASSWORD, "remember_me": True},
    )
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    # The rotated cookie keeps the choice: persistence lives on the session row, not the
    # cookie, so a refresh cannot silently downgrade or upgrade it.
    assert (
        f"max-age={get_settings().session_ttl_days * 86400}" in resp.headers["set-cookie"].lower()
    )


async def test_idle_session_cannot_be_refreshed(api, db) -> None:
    """A cookie left behind in a browser stops working once the session goes idle."""
    client = api()
    tokens = await register(client)
    idle = timedelta(hours=get_settings().refresh_idle_timeout_hours + 1)

    async with db() as session:
        await session.execute(update(UserSession).values(last_used_at=datetime.now(UTC) - idle))
        await session.commit()

    assert (await client.post("/api/auth/refresh")).status_code == 401
    # The session is ended, not merely refused, so the still-valid JWT dies with it.
    assert (
        await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))
    ).status_code == 401
    async with db() as session:
        row = await session.scalar(select(UserSession))
    assert row.revoke_reason == "idle_timeout"


async def test_revocation_beats_a_concurrent_request_repopulating_the_cache(api, db) -> None:
    """Revoking tombstones the session cache, so an in-flight request cannot un-revoke it.

    Deleting the key instead left a five-minute hole: the revoking transaction had not
    committed, so a concurrent reader saw an active row and cached it.
    """
    from app.auth.service import SESSION_CACHE_TTL_SECONDS, revoke_session, session_is_active
    from app.cache.redis import redis_client

    client = api()
    tokens = await register(client)
    headers = bearer(tokens["access_token"])
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    async with db() as session:
        row = await session.scalar(select(UserSession))
        session_id, user_id = row.id, row.user_id

    async with db() as revoking:
        await revoke_session(revoking, redis_client, session_id, reason="logout")
        # Still uncommitted: another request reading its own snapshot sees an active row.
        async with db() as reader:
            assert await session_is_active(reader, redis_client, session_id, user_id) is False
        await revoking.commit()

    assert await redis_client.ttl(f"nis:session:{session_id}") <= SESSION_CACHE_TTL_SECONDS
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
