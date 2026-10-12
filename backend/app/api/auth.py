import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import select

from app.auth import google, sso, twofactor
from app.auth import service as auth
from app.auth.deps import CurrentUserDep, client_info, get_current_user
from app.auth.security import hash_token
from app.auth.validation import (
    PASSWORD_MAX,
    ValidationError,
    clean_name,
    display_phone,
    normalize_phone,
    password_problems,
)
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.db.session import SessionDep
from app.email import service as emails
from app.models import User, UserSession
from app.profiles.service import create_profile, template_input
from app.scoring.service import rescore_profile
from app.scoring.templates import DEFAULT_TEMPLATE

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)
settings = get_settings()

RefreshCookie = Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)]


class RegisterIn(BaseModel):
    full_name: str = Field(max_length=200)
    phone: str = Field(max_length=32)
    email: EmailStr
    password: str = Field(max_length=PASSWORD_MAX)
    confirm_password: str = Field(max_length=PASSWORD_MAX)
    accept_terms: bool
    accept_risk_notice: bool
    marketing_opt_in: bool = False

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return validated(clean_name, value)

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str) -> str:
        return validated(normalize_phone, value)

    @model_validator(mode="after")
    def _rules(self) -> "RegisterIn":
        if not self.accept_terms:
            raise ValueError("Accept the Terms and Privacy Policy to create an account.")
        if not self.accept_risk_notice:
            raise ValueError("Confirm you understand News Intelligence is not investment advice.")
        if self.password != self.confirm_password:
            raise ValueError("The passwords don't match.")
        problems = password_problems(self.password, email=self.email, name=self.full_name)
        if problems:
            raise ValueError(" ".join(problems))
        return self


def validated(check, value):  # noqa: ANN001, ANN202
    try:
        return check(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from None


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    phone: str | None  # formatted for display, e.g. +91 98765 43210
    is_admin: bool
    email_verified: bool
    phone_verified: bool
    two_factor_enabled: bool
    has_password: bool  # False for accounts created with Google until a password is set
    marketing_opt_in: bool
    created_at: datetime
    last_login_at: datetime | None
    password_changed_at: datetime | None

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            phone=display_phone(user.phone),
            is_admin=user.is_admin,
            email_verified=user.email_verified_at is not None,
            phone_verified=user.phone_verified_at is not None,
            two_factor_enabled=user.totp_enabled_at is not None,
            has_password=user.has_password,
            marketing_opt_in=user.marketing_opt_in,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            password_changed_at=user.password_changed_at,
        )


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut | None = None


class MfaRequiredOut(BaseModel):
    """Password accepted; now send the authenticator code to /auth/login/2fa."""

    mfa_required: bool = True
    mfa_token: str
    expires_in: int = twofactor.CHALLENGE_TTL


class MfaIn(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=100)
    code: str = Field(min_length=6, max_length=20)


class SessionOut(BaseModel):
    id: uuid.UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    current: bool


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,  # unreadable from JavaScript, so XSS can't steal it
        secure=settings.secure_cookies,
        samesite="strict",  # never sent on cross-site requests (CSRF)
        path="/api/auth",  # only sent to the auth endpoints, not to every API call
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.refresh_cookie_name,
        path="/api/auth",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="strict",
    )


def _http_error(err: auth.AuthError) -> HTTPException:
    return HTTPException(err.status, detail={"code": err.code, "message": err.message})


async def _sign_in(
    db: SessionDep, request: Request, response: Response, email: str, password: str
) -> TokenOut | MfaRequiredOut:
    client = client_info(request)
    if not await auth.login_attempt_allowed(redis_client, email, client.ip_address):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "too_many_attempts", "message": "Too many attempts. Try later."},
        )
    try:
        user, tokens = await auth.login(db, email=email, password=password, client=client)
    except auth.AuthError as err:
        raise _http_error(err) from None
    await db.commit()
    if tokens is None:  # two-step sign-in: the attempt counter clears after the code step
        return MfaRequiredOut(mfa_token=await twofactor.start_challenge(redis_client, user.id))
    await auth.clear_login_attempts(redis_client, email, client.ip_address)
    _set_refresh_cookie(response, tokens.refresh_token)
    return TokenOut(
        access_token=tokens.access_token, expires_in=tokens.expires_in, user=UserOut.of(user)
    )


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn, db: SessionDep, request: Request, response: Response
) -> TokenOut:
    """Create an account, sign in, and give it a ready-made trader profile.

    Every rule the sign-up form shows is enforced here too: a browser can be bypassed.
    """
    signup = auth.Signup(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
        marketing_opt_in=body.marketing_opt_in,
    )
    try:
        user, tokens = await auth.register(db, signup, client=client_info(request))
    except auth.AuthError as err:
        raise _http_error(err) from None
    profile = await create_profile(
        db, user.id, template_input(DEFAULT_TEMPLATE), template=DEFAULT_TEMPLATE
    )
    await rescore_profile(db, profile)
    await emails.send_verification(db, user)
    await db.commit()
    await db.refresh(user)
    _set_refresh_cookie(response, tokens.refresh_token)
    return TokenOut(
        access_token=tokens.access_token, expires_in=tokens.expires_in, user=UserOut.of(user)
    )


@router.post("/login", response_model=TokenOut | MfaRequiredOut)
async def login(
    body: LoginIn, db: SessionDep, request: Request, response: Response
) -> TokenOut | MfaRequiredOut:
    """Sign in. With two-step sign-in on, answers {mfa_required, mfa_token} instead."""
    return await _sign_in(db, request, response, body.email, body.password)


@router.post("/login/2fa", response_model=TokenOut)
async def login_second_step(
    body: MfaIn, db: SessionDep, request: Request, response: Response
) -> TokenOut:
    """The authenticator code (or a recovery code) after the password step."""
    user_id = await twofactor.challenge_user(redis_client, body.mfa_token)
    if user_id is None:
        raise HTTPException(
            401, detail={"code": "mfa_expired", "message": "Sign in again: that step expired."}
        )
    user = await db.get(User, user_id, with_for_update=True)
    if user is None or not user.is_active:
        raise HTTPException(401, detail={"code": "mfa_expired", "message": "Sign in again."})
    if not await twofactor.check_code(db, user, body.code):
        await db.commit()
        raise HTTPException(
            400, detail={"code": "wrong_code", "message": twofactor.WRONG_CODE.message}
        )
    client = client_info(request)
    tokens = await auth.finish_login(db, user, client)
    await db.commit()
    await twofactor.end_challenge(redis_client, body.mfa_token)
    await auth.clear_login_attempts(redis_client, user.email, client.ip_address)
    _set_refresh_cookie(response, tokens.refresh_token)
    return TokenOut(
        access_token=tokens.access_token, expires_in=tokens.expires_in, user=UserOut.of(user)
    )


@router.post("/token", response_model=TokenOut, include_in_schema=True)
async def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: SessionDep,
    request: Request,
    response: Response,
) -> TokenOut:
    """Form-encoded login (OAuth2 password flow) so the API docs' Authorize button works.
    Accounts with two-step sign-in must sign in through the app."""
    result = await _sign_in(db, request, response, form.username, form.password)
    if isinstance(result, MfaRequiredOut):
        raise HTTPException(
            400,
            detail={"code": "mfa_required", "message": "This account uses two-step sign-in."},
        )
    return result


@router.post("/refresh", response_model=TokenOut, responses={401: {"description": "Sign in again"}})
async def refresh(
    db: SessionDep, request: Request, response: Response, refresh_token: RefreshCookie = None
) -> TokenOut | JSONResponse:
    """Exchange the refresh cookie for a new access token (and a rotated cookie)."""
    try:
        tokens = await auth.refresh(
            db, redis_client, refresh_token=refresh_token, client=client_info(request)
        )
    except auth.AuthError as err:
        await db.commit()  # keep a reuse-detection revocation
        failed = JSONResponse(
            status_code=err.status, content={"detail": {"code": err.code, "message": err.message}}
        )
        clear_refresh_cookie(failed)
        return failed
    await db.commit()
    if tokens.refresh_token:
        _set_refresh_cookie(response, tokens.refresh_token)
    return TokenOut(access_token=tokens.access_token, expires_in=tokens.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    db: SessionDep,
    response: Response,
    request: Request,
    refresh_token: RefreshCookie = None,
) -> None:
    """End this device's session. Works with the refresh cookie, the access token, or both."""
    session_ids: set[uuid.UUID] = set()
    if refresh_token:
        found = await db.scalar(
            select(UserSession.id).where(UserSession.token_hash == hash_token(refresh_token))
        )
        if found:
            session_ids.add(found)
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if bearer:
        try:
            session_ids.add((await get_current_user(db, bearer)).session_id)
        except HTTPException:
            pass  # an expired or revoked access token: nothing more to end
    for session_id in session_ids:
        await auth.revoke_session(db, redis_client, session_id, reason="logout")
    await db.commit()
    clear_refresh_cookie(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(current: CurrentUserDep, db: SessionDep, response: Response) -> None:
    """Sign out every device, including this one."""
    await auth.revoke_all_sessions(db, redis_client, current.user_id, reason="logout_all")
    await db.commit()
    clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(current: CurrentUserDep, db: SessionDep) -> UserOut:
    user = await db.get(User, current.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    return UserOut.of(user)


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(current: CurrentUserDep, db: SessionDep) -> list[SessionOut]:
    """Devices currently signed in to this account."""
    return [
        SessionOut(
            id=s.id,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
            created_at=s.created_at,
            last_used_at=s.last_used_at,
            current=s.id == current.session_id,
        )
        for s in await auth.active_sessions(db, current.user_id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(session_id: uuid.UUID, current: CurrentUserDep, db: SessionDep) -> None:
    """Sign out one device."""
    owner = await db.scalar(select(UserSession.user_id).where(UserSession.id == session_id))
    if owner != current.user_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "session_not_found", "message": "Not found."}
        )
    await auth.revoke_session(db, redis_client, session_id, reason="revoked_by_user")
    await db.commit()


# --------------------------------------------------------------------------- email links


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=100)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(TokenIn):
    password: str = Field(max_length=PASSWORD_MAX)
    confirm_password: str = Field(max_length=PASSWORD_MAX)


def _slow_down() -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "slow_down",
                "message": "Too many requests. Wait a few minutes and try again."},
    )  # fmt: skip


def _bad_link(what: str) -> HTTPException:
    return HTTPException(
        400,
        detail={"code": "invalid_link",
                "message": f"This {what} link is invalid or has expired. Request a new one."},
    )  # fmt: skip


@router.post("/verify-email")
async def verify_email(body: TokenIn, db: SessionDep, request: Request) -> dict:
    """Confirm the email address from the link we sent. Works signed in or not."""
    ip = client_info(request).ip_address
    if not await auth.within_rate(redis_client, f"verify:{ip}", 30, 3600):
        raise _slow_down()
    found = await emails.consume_token(db, body.token, emails.VERIFY_EMAIL)
    if found is None:
        raise _bad_link("confirmation")
    user, _ = found
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    await db.commit()
    return {"verified": True, "email": user.email}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(current: CurrentUserDep, db: SessionDep) -> dict:
    user = await db.get(User, current.user_id)
    if user.email_verified_at is not None:
        return {"sent": False, "already_verified": True}
    if not (
        await auth.within_rate(redis_client, f"resend:{user.id}:min", 1, 60)
        and await auth.within_rate(redis_client, f"resend:{user.id}:hour", 5, 3600)
    ):
        raise _slow_down()
    await emails.send_verification(db, user)
    await db.commit()
    return {"sent": True, "already_verified": False}


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(body: ForgotIn, db: SessionDep, request: Request) -> dict:
    """Email a reset link. The answer is the same whether or not the account exists, so this
    can't be used to find out who has an account."""
    email = str(body.email).strip().lower()
    ip = client_info(request).ip_address
    if not (
        await auth.within_rate(redis_client, f"forgot:ip:{ip}", 20, 3600)
        and await auth.within_rate(redis_client, f"forgot:email:{email}", 3, 3600)
    ):
        raise _slow_down()
    user = await db.scalar(select(User).where(User.email == email))
    if user is not None and user.is_active:
        await emails.send_password_reset(db, user)
        await db.commit()
    return {"sent": True}


@router.post("/reset-password")
async def reset_password(body: ResetIn, db: SessionDep, request: Request) -> dict:
    """Set a new password with the emailed link. Signs out every device; sign in again."""
    ip = client_info(request).ip_address
    if not await auth.within_rate(redis_client, f"reset:{ip}", 30, 3600):
        raise _slow_down()
    if body.password != body.confirm_password:
        raise HTTPException(
            422, detail={"code": "mismatch", "message": "The passwords don't match."}
        )
    found = await emails.consume_token(db, body.token, emails.RESET_PASSWORD)
    if found is None:
        raise _bad_link("reset")
    user, _ = found
    problems = password_problems(body.password, email=user.email, name=user.full_name or "")
    if problems:
        await db.rollback()  # nothing changed: the link stays usable
        raise HTTPException(422, detail={"code": "weak_password", "message": " ".join(problems)})
    await auth.reset_password(db, redis_client, user, body.password)
    await emails.notify_password_changed(db, user)
    await db.commit()
    await auth.clear_login_attempts(redis_client, user.email, ip)
    return {"reset": True}


# --------------------------------------------------------------------------- Google


def google_client() -> google.GoogleClient:
    """Indirection so tests can replace the calls to Google."""
    return google.GoogleClient()


def _to_login(code: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?sso_error={code}", status_code=303)
    response.delete_cookie(google.STATE_COOKIE, path="/api/auth/google")
    return response


@router.get("/google/start", include_in_schema=False)
async def google_start(
    request: Request,
    next: Annotated[str | None, Query(max_length=300)] = None,  # noqa: A002
    consent: bool = False,
) -> RedirectResponse:
    """Send the browser to Google. `consent=true` only from the sign-up page, after the
    person ticked the Terms and the risk notice (needed if this creates an account)."""
    if not settings.google_configured:
        return _to_login("not_configured")
    ip = client_info(request).ip_address
    if not await auth.within_rate(redis_client, f"google:{ip}", 30, 600):
        return _to_login("slow_down")
    url, state = await google.start(redis_client, next_path=next, consent=consent)
    response = RedirectResponse(url, status_code=303)
    # Binds the flow to this browser. Lax (not Strict): Google's redirect back to us is a
    # cross-site top-level navigation, which Lax cookies accompany and Strict ones don't.
    response.set_cookie(
        google.STATE_COOKIE, state, max_age=google.FLOW_TTL, httponly=True,
        secure=settings.secure_cookies, samesite="lax", path="/api/auth/google",
    )  # fmt: skip
    return response


@router.get("/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    db: SessionDep,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    """Google sends the browser back here. Verify everything, then open our own session."""
    cookie_state = request.cookies.get(google.STATE_COOKIE)
    try:
        flow = await google.take_flow(redis_client, state, cookie_state)
        if error or not code:  # e.g. the person pressed Cancel on Google's screen
            raise google.GoogleAuthError("cancelled", error or "no code")
        client = google_client()
        id_token = await client.exchange(code, flow.verifier)
        identity = google.verify_id_token(client, id_token, flow.nonce)
        user, created = await sso.resolve_google_user(db, redis_client, identity, flow)
        if created:  # same start as a password sign-up: a ready-made trader profile
            profile = await create_profile(
                db, user.id, template_input(DEFAULT_TEMPLATE), template=DEFAULT_TEMPLATE
            )
            await rescore_profile(db, profile)
    except google.GoogleAuthError as exc:
        await db.rollback()
        log.warning("Google sign-in failed: %s (%s)", exc.code, exc)
        if exc.code == "needs_consent":
            response = RedirectResponse("/signup?sso=needs_consent", status_code=303)
            response.delete_cookie(google.STATE_COOKIE, path="/api/auth/google")
            return response
        return _to_login(exc.code)

    if user.totp_enabled_at is not None:
        # Two-step sign-in still applies: the code page picks the challenge up from the
        # URL fragment, which browsers never send to any server.
        await db.commit()
        token = await twofactor.start_challenge(redis_client, user.id)
        response = RedirectResponse(f"/login?next={quote(flow.next)}#mfa={token}", status_code=303)
    else:
        tokens = await auth.finish_login(db, user, client_info(request))
        await db.commit()
        target = "/feed?welcome=1" if created and flow.next == "/feed" else flow.next
        response = RedirectResponse(target, status_code=303)
        _set_refresh_cookie(response, tokens.refresh_token)
    response.delete_cookie(google.STATE_COOKIE, path="/api/auth/google")
    log.info("Google sign-in for user %s%s", user.id, " (new account)" if created else "")
    return response
