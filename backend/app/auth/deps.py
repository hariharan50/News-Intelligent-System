import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import InvalidTokenError, decode_access_token
from app.auth.service import ClientInfo, session_is_active
from app.cache.redis import redis_client
from app.db.session import SessionDep

# tokenUrl makes Swagger UI's "Authorize" button work with the form login endpoint.
_bearer = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={"code": "not_authenticated", "message": "Sign in to continue."},
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: uuid.UUID
    session_id: uuid.UUID
    token_expires_at: datetime


async def authenticate(db: AsyncSession, token: str | None) -> CurrentUser:
    """Valid JWT *and* a live session: revoked sessions fail immediately."""
    if not token:
        raise UNAUTHENTICATED
    try:
        claims = decode_access_token(token)
    except InvalidTokenError:
        raise UNAUTHENTICATED from None
    if not await session_is_active(db, redis_client, claims.session_id, claims.user_id):
        raise UNAUTHENTICATED
    return CurrentUser(claims.user_id, claims.session_id, claims.expires_at)


async def get_current_user(
    db: SessionDep, token: Annotated[str | None, Depends(_bearer)]
) -> CurrentUser:
    return await authenticate(db, token)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
BearerToken = Annotated[str | None, Depends(_bearer)]


def client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
