"""My account: details, password change, deletion (needs Docker PostgreSQL/Redis)."""

import pytest
from sqlalchemy import func, select

from app.auth.validation import ValidationError, normalize_phone, password_problems
from app.models import Profile, User
from tests.conftest import TEST_PASSWORD, signup_body

pytestmark = pytest.mark.usefixtures("db")

NEW_PASSWORD = "brand new secret 77"


async def signed_in(api, email: str = "asha@example.com", **overrides):
    client = api()
    resp = await client.post("/api/auth/register", json=signup_body(email, **overrides))
    assert resp.status_code == 201, resp.text
    return client, {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_account_shows_the_users_details(api) -> None:
    client, headers = await signed_in(api, phone="+44 20 7946 0958")
    account = (await client.get("/api/account", headers=headers)).json()
    assert account["full_name"] == "Asha Rao"
    assert account["email"] == "asha@example.com"
    assert account["phone"] == "+44 20 7946 0958"
    assert account["email_verified"] is False and account["phone_verified"] is False
    assert account["created_at"] and account["password_changed_at"]


async def test_update_name_phone_and_marketing(api) -> None:
    client, headers = await signed_in(api)
    resp = await client.patch(
        "/api/account", headers=headers,
        json={"full_name": "  Asha   K Rao ", "phone": "99887 66554", "marketing_opt_in": True},
    )  # fmt: skip
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["full_name"], body["phone"], body["marketing_opt_in"]) == (
        "Asha K Rao", "+91 99887 66554", True,
    )  # fmt: skip

    bad = await client.patch("/api/account", headers=headers, json={"phone": "123"})
    assert bad.status_code == 422


async def test_cannot_take_someone_elses_phone(api) -> None:
    await signed_in(api, "first@example.com", phone="98765 11111")
    client, headers = await signed_in(api, "second@example.com")
    resp = await client.patch("/api/account", headers=headers, json={"phone": "+919876511111"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "phone_taken"


async def test_change_password_signs_out_other_devices(api) -> None:
    laptop, laptop_headers = await signed_in(api)
    phone = api()
    login = await phone.post(
        "/api/auth/login", json={"email": "asha@example.com", "password": TEST_PASSWORD}
    )
    phone_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    wrong = await laptop.post(
        "/api/account/password", headers=laptop_headers,
        json={"current_password": "not it 123", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )  # fmt: skip
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "wrong_password"

    weak = await laptop.post(
        "/api/account/password", headers=laptop_headers,
        json={"current_password": TEST_PASSWORD, "new_password": "weakweak", "confirm_password": "weakweak"},
    )  # fmt: skip
    assert weak.status_code == 422

    mismatch = await laptop.post(
        "/api/account/password", headers=laptop_headers,
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": "other 12345"},
    )  # fmt: skip
    assert mismatch.status_code == 422

    ok = await laptop.post(
        "/api/account/password", headers=laptop_headers,
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )  # fmt: skip
    assert ok.status_code == 200 and ok.json()["other_devices_signed_out"] == 1

    assert (
        await laptop.get("/api/account", headers=laptop_headers)
    ).status_code == 200  # this device stays
    assert (
        await phone.get("/api/account", headers=phone_headers)
    ).status_code == 401  # others are out
    old = await api().post(
        "/api/auth/login", json={"email": "asha@example.com", "password": TEST_PASSWORD}
    )
    new = await api().post(
        "/api/auth/login", json={"email": "asha@example.com", "password": NEW_PASSWORD}
    )
    assert (old.status_code, new.status_code) == (401, 200)


async def test_delete_account_removes_everything(api, db) -> None:
    client, headers = await signed_in(api)
    wrong = await client.post(
        "/api/account/delete", headers=headers, json={"password": "nope 12345", "confirm": "DELETE"}
    )
    assert wrong.status_code == 400
    unconfirmed = await client.post(
        "/api/account/delete", headers=headers, json={"password": TEST_PASSWORD, "confirm": "yes"}
    )
    assert unconfirmed.status_code == 422

    gone = await client.post(
        "/api/account/delete",
        headers=headers,
        json={"password": TEST_PASSWORD, "confirm": "DELETE"},
    )
    assert gone.status_code == 204
    assert (await client.get("/api/account", headers=headers)).status_code == 401
    async with db() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(Profile)) == 0
    # The email and phone can be used again.
    again = await api().post("/api/auth/register", json=signup_body("asha@example.com"))
    assert again.status_code == 201


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("98765 43210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("(+44) 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "98765abcde", "+1 555", "9" * 40])
def test_normalize_phone_rejects(raw: str) -> None:
    with pytest.raises(ValidationError):
        normalize_phone(raw)


def test_password_rules() -> None:
    assert (
        password_problems("correct horse battery 42", email="asha@example.com", name="Asha Rao")
        == []
    )
    assert password_problems("aaaaaaaaaa1") != []  # repetitive
    assert password_problems("Rao-secret-2026", name="Asha Rao") == []  # 3-letter parts are fine
    assert password_problems("ashaisgreat123", email="asha@example.com") != []
