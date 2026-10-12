"""The signed-in user's own account: details, password, two-step sign-in, deletion."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select

from app.api.auth import UserOut, clear_refresh_cookie, validated
from app.auth import service as auth
from app.auth import twofactor
from app.auth.deps import CurrentUserDep
from app.auth.security import verify_password
from app.auth.validation import PASSWORD_MAX, clean_name, normalize_phone, password_problems
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.db.session import SessionDep
from app.email import service as emails
from app.models import User, UserIdentity

router = APIRouter(prefix="/account", tags=["account"])


class AccountUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    marketing_opt_in: bool | None = None

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else validated(clean_name, value)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return None if value is None else validated(normalize_phone, value)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    new_password: str = Field(max_length=PASSWORD_MAX)
    confirm_password: str = Field(max_length=PASSWORD_MAX)

    @model_validator(mode="after")
    def _match(self) -> "PasswordChange":
        if self.new_password != self.confirm_password:
            raise ValueError("The new passwords don't match.")
        return self


class AccountDelete(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    confirm: Literal["DELETE"]


def _error(err: auth.AuthError) -> HTTPException:
    return HTTPException(err.status, detail={"code": err.code, "message": err.message})


async def _user(db: SessionDep, current: CurrentUserDep) -> User:
    user = await db.get(User, current.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return user


@router.get("", response_model=UserOut)
async def get_account(current: CurrentUserDep, db: SessionDep) -> UserOut:
    return UserOut.of(await _user(db, current))


@router.patch("", response_model=UserOut)
async def update_account(body: AccountUpdate, current: CurrentUserDep, db: SessionDep) -> UserOut:
    user = await _user(db, current)
    if body.phone is not None and body.phone != user.phone:
        taken = await db.scalar(select(User.id).where(User.phone == body.phone, User.id != user.id))
        if taken:
            raise _error(auth.PHONE_TAKEN)
        user.phone = body.phone
        user.phone_verified_at = None  # a new number has to be verified again
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.marketing_opt_in is not None:
        user.marketing_opt_in = body.marketing_opt_in
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/password")
async def change_password(body: PasswordChange, current: CurrentUserDep, db: SessionDep) -> dict:
    """Change your password. Every other signed-in device is signed out."""
    user = await _user(db, current)
    problems = password_problems(body.new_password, email=user.email, name=user.full_name or "")
    if problems:
        raise HTTPException(422, detail={"code": "weak_password", "message": " ".join(problems)})
    try:
        ended = await auth.change_password(
            db,
            redis_client,
            user_id=current.user_id,
            current_session=current.session_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except auth.AuthError as err:
        raise _error(err) from None
    await emails.notify_password_changed(db, user)
    await db.commit()
    return {"changed": True, "other_devices_signed_out": ended}


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: AccountDelete, current: CurrentUserDep, db: SessionDep, response: Response
) -> None:
    """Permanently delete your account, profiles, alerts and saved stories."""
    try:
        await auth.delete_account(db, redis_client, user_id=current.user_id, password=body.password)
    except auth.AuthError as err:
        raise _error(err) from None
    await db.commit()
    clear_refresh_cookie(response)


# --------------------------------------------------------------------------- two-step sign-in


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class DisableTwoFactorIn(CodeIn):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


def _2fa_error(err: twofactor.TwoFactorError) -> HTTPException:
    return HTTPException(err.status, detail={"code": err.code, "message": err.message})


@router.get("/2fa")
async def two_factor_status(current: CurrentUserDep, db: SessionDep) -> dict:
    user = await db.get(User, current.user_id)
    return {
        "enabled": user.totp_enabled_at is not None,
        "enabled_at": user.totp_enabled_at,
        "recovery_codes_left": await twofactor.codes_left(db, user.id),
        "required": bool(user.is_admin and get_settings().admin_needs_2fa),
    }


@router.post("/2fa/setup")
async def two_factor_setup(current: CurrentUserDep, db: SessionDep) -> dict:
    """A new secret and otpauth:// link (shown as a QR code). Nothing changes until /enable."""
    user = await db.get(User, current.user_id)
    if user.totp_enabled_at is not None:
        raise HTTPException(
            409, detail={"code": "already_enabled", "message": "Two-step sign-in is already on."}
        )
    return await twofactor.begin_setup(redis_client, user)


@router.post("/2fa/enable")
async def two_factor_enable(body: CodeIn, current: CurrentUserDep, db: SessionDep) -> dict:
    """Confirm with the first code. Signs out other devices; returns recovery codes once."""
    user = await db.get(User, current.user_id, with_for_update=True)
    try:
        codes = await twofactor.enable(db, redis_client, user, body.code)
    except twofactor.TwoFactorError as err:
        raise _2fa_error(err) from None
    await auth.revoke_all_sessions(
        db, redis_client, user.id, reason="2fa_enabled", keep=current.session_id
    )
    await db.commit()
    return {"enabled": True, "recovery_codes": codes}


@router.post("/2fa/disable")
async def two_factor_disable(
    body: DisableTwoFactorIn, current: CurrentUserDep, db: SessionDep
) -> dict:
    user = await db.get(User, current.user_id, with_for_update=True)
    if not verify_password(user.password_hash, body.password):
        raise HTTPException(
            400, detail={"code": "wrong_password", "message": "Your password is incorrect."}
        )
    try:
        await twofactor.disable(db, user, body.code)
    except twofactor.TwoFactorError as err:
        await db.commit()  # keep the replay counter if a recovery code was consumed
        raise _2fa_error(err) from None
    await db.commit()
    return {"enabled": False}


@router.post("/2fa/recovery-codes")
async def two_factor_new_codes(body: CodeIn, current: CurrentUserDep, db: SessionDep) -> dict:
    """Replace all recovery codes (the old ones stop working)."""
    user = await db.get(User, current.user_id, with_for_update=True)
    try:
        codes = await twofactor.regenerate_codes(db, user, body.code)
    except twofactor.TwoFactorError as err:
        raise _2fa_error(err) from None
    await db.commit()
    return {"recovery_codes": codes}


# --------------------------------------------------------------------------- sign-in methods


@router.get("/sign-in-methods")
async def sign_in_methods(current: CurrentUserDep, db: SessionDep) -> dict:
    user = await db.get(User, current.user_id)
    google = await db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == "google"
        )
    )
    return {
        "password": user.has_password,
        "google": {
            "email": google.email,
            "connected_at": google.created_at,
            "last_used_at": google.last_used_at,
        }
        if google
        else None,
    }


@router.delete("/sign-in-methods/google", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_google(current: CurrentUserDep, db: SessionDep) -> None:
    """Stop signing in with Google. Needs a password first, or the account would be locked."""
    user = await db.get(User, current.user_id, with_for_update=True)
    if not user.has_password:
        raise HTTPException(
            409,
            detail={"code": "no_password",
                    "message": "Set a password first (use “Forgot password” on the sign-in page)."},
        )  # fmt: skip
    await db.execute(
        delete(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == "google"
        )
    )
    await db.commit()
