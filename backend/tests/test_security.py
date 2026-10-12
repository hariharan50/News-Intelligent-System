"""Two-step sign-in, admin audit log and production setting checks."""

import base64
import time

import pytest
from pydantic import ValidationError

from app.auth import totp
from app.cache.redis import redis_client
from app.core.config import Settings, get_settings
from tests.conftest import TEST_PASSWORD
from tests.test_alerts_telegram_admin import make_admin
from tests.test_profiles_feed_api import sign_up

# RFC 6238 test secret ("12345678901234567890") in base32.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


# --------------------------------------------------------------------------- unit


def test_totp_matches_rfc_6238_and_refuses_replays() -> None:
    assert totp.current_code(RFC_SECRET, at=59) == "287082"
    assert totp.current_code(RFC_SECRET, at=1111111109) == "081804"
    counter = totp.verify(RFC_SECRET, "287 082", last_counter=None, at=59)
    assert counter == 1
    assert totp.verify(RFC_SECRET, "287082", last_counter=counter, at=59) is None  # replay
    assert totp.verify(RFC_SECRET, "000000", last_counter=None, at=59) is None
    assert totp.verify(RFC_SECRET, "12345", last_counter=None, at=59) is None


def test_secrets_are_encrypted_at_rest() -> None:
    sealed = totp.encrypt(RFC_SECRET)
    assert RFC_SECRET not in sealed
    assert totp.decrypt(sealed) == RFC_SECRET
    assert totp.encrypt(RFC_SECRET) != sealed  # random nonce


def test_recovery_codes_are_hashed_loosely() -> None:
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert totp.hash_recovery_code("AB12-CD34") == totp.hash_recovery_code(" ab12cd34 ")


def test_production_refuses_unsafe_settings() -> None:
    with pytest.raises(ValidationError, match="APP_BASE_URL must be https"):
        Settings(environment="production", jwt_secret="x" * 40, _env_file=None)
    good = Settings(
        environment="production", jwt_secret="x" * 40, app_base_url="https://newsintel.in",
        cors_origins=["https://newsintel.in"], data_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        email_provider="smtp", _env_file=None,
    )  # fmt: skip
    assert good.production_problems() == [] and good.admin_needs_2fa is True


# --------------------------------------------------------------------------- two-step sign-in


def next_code(secret: str) -> str:
    """The code for the next 30-second step: accepted (clock drift) but not yet used."""
    return totp.current_code(secret, at=time.time() + 30)


async def turn_on_2fa(client, headers) -> tuple[str, list[str]]:
    setup = (await client.post("/api/account/2fa/setup", headers=headers)).json()
    assert setup["otpauth_url"].startswith(
        "otpauth://totp/News%20Intelligence:trader%40example.com?"
    )
    resp = await client.post(
        "/api/account/2fa/enable",
        json={"code": totp.current_code(setup["secret"])},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return setup["secret"], resp.json()["recovery_codes"]


async def password_step(api) -> tuple[object, dict]:
    client = api()
    resp = await client.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": TEST_PASSWORD}
    )
    assert resp.status_code == 200
    return client, resp.json()


async def test_two_step_sign_in(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    other = api()
    other_login = await other.post(
        "/api/auth/login", json={"email": "trader@example.com", "password": TEST_PASSWORD}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    wrong = await client.post("/api/account/2fa/enable", json={"code": "123456"}, headers=headers)
    assert wrong.status_code == 400  # no setup started
    secret, codes = await turn_on_2fa(client, headers)
    assert len(codes) == 10
    me = (await client.get("/api/auth/me", headers=headers)).json()
    assert me["two_factor_enabled"] is True
    # Other devices were signed out; this one stays.
    assert (await other.get("/api/auth/me", headers=other_headers)).status_code == 401

    # Password alone no longer signs in.
    login, step = await password_step(api)
    assert step["mfa_required"] is True and "access_token" not in step
    bad = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": "000000"}
    )
    assert bad.status_code == 400
    ok = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": next_code(secret)}
    )
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert "nis_refresh" in ok.headers.get("set-cookie", "")
    # The challenge is single-use.
    again = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": next_code(secret)}
    )
    assert again.status_code == 401

    # A recovery code works once.
    login, step = await password_step(api)
    ok = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": codes[0].upper()}
    )
    assert ok.status_code == 200
    login, step = await password_step(api)
    reused = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": codes[0]}
    )
    assert reused.status_code == 400
    state = (await client.get("/api/account/2fa", headers=headers)).json()
    assert state["enabled"] is True and state["recovery_codes_left"] == 9

    # The docs' form login refuses 2FA accounts instead of skipping the code.
    form = await api().post(
        "/api/auth/token", data={"username": "trader@example.com", "password": TEST_PASSWORD}
    )
    assert form.status_code == 400 and form.json()["detail"]["code"] == "mfa_required"


async def test_challenge_gives_up_after_five_wrong_codes(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    secret, _ = await turn_on_2fa(client, headers)
    login, step = await password_step(api)
    for _ in range(5):
        await login.post(
            "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": "000000"}
        )
    resp = await login.post(
        "/api/auth/login/2fa", json={"mfa_token": step["mfa_token"], "code": next_code(secret)}
    )
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "mfa_expired"


async def test_turning_2fa_off_needs_password_and_code(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    secret, codes = await turn_on_2fa(client, headers)
    resp = await client.post(
        "/api/account/2fa/disable",
        json={"password": "wrong password 1", "code": next_code(secret)},
        headers=headers,
    )
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "wrong_password"
    resp = await client.post(
        "/api/account/2fa/disable",
        json={"password": TEST_PASSWORD, "code": "000000"},
        headers=headers,
    )
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "wrong_code"
    resp = await client.post(
        "/api/account/2fa/recovery-codes", json={"code": codes[1]}, headers=headers
    )
    new_codes = resp.json()["recovery_codes"]
    assert set(new_codes).isdisjoint(codes)
    resp = await client.post(
        "/api/account/2fa/disable",
        json={"password": TEST_PASSWORD, "code": new_codes[0]},
        headers=headers,
    )
    assert resp.status_code == 200
    _, step = await password_step(api)
    assert step.get("access_token")  # back to password-only


# --------------------------------------------------------------------------- admin


async def test_admins_must_use_2fa_when_required(api, db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_require_2fa", True)
    client = api()
    headers = await sign_up(client)
    await make_admin(db, "trader@example.com")
    resp = await client.get("/api/admin/overview", headers=headers)
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "admin_2fa_required"
    assert (await client.get("/api/account/2fa", headers=headers)).json()["required"] is True
    await turn_on_2fa(client, headers)
    assert (await client.get("/api/admin/overview", headers=headers)).status_code == 200


async def test_admin_actions_are_audited(api, db) -> None:
    admin = api()
    admin_h = await sign_up(admin, "admin@example.com")
    await make_admin(db, "admin@example.com")
    user = api()
    await sign_up(user, "member@example.com")
    member_id = (
        await admin.get("/api/admin/users", params={"q": "member"}, headers=admin_h)
    ).json()["items"][0]["id"]

    await admin.patch(f"/api/admin/users/{member_id}", json={"is_active": False}, headers=admin_h)
    await admin.patch(
        f"/api/admin/users/{member_id}", json={"is_active": False}, headers=admin_h
    )  # no change
    await admin.put("/api/admin/ai", json={"enabled": False}, headers=admin_h)
    await redis_client.delete("nis:ai:enabled")

    log = (await admin.get("/api/admin/audit", headers=admin_h)).json()
    assert log["total"] == 2
    ai_entry, user_entry = log["items"]
    assert ai_entry["action"] == "ai.switch" and ai_entry["details"] == {"enabled": False}
    assert user_entry["action"] == "user.update" and user_entry["actor"] == "admin@example.com"
    assert user_entry["target_id"] == member_id
    assert user_entry["details"]["is_active"] == {"from": True, "to": False}
    assert user_entry["details"]["email"] == "member@example.com"

    only_users = (
        await admin.get("/api/admin/audit", params={"action": "user."}, headers=admin_h)
    ).json()
    assert only_users["total"] == 1
    member_h = await sign_up(api(), "other@example.com")
    assert (await api().get("/api/admin/audit", headers=member_h)).status_code == 403
