"""Live updates over Server-Sent Events.

The browser reads this with fetch() (not EventSource) so the access token travels in the
Authorization header, never in the URL. The stream ends when the access token expires;
the client refreshes its token and reconnects.

Events:
  ready   {}                                      once, after subscribing
  story   {profile_id, story_id, score, tier, alert, new}
  reauth  {}                                      token expired or session revoked: reconnect
Comment lines (": ping") keep proxies from closing an idle connection.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.auth.deps import BearerToken, CurrentUser, authenticate
from app.auth.service import session_is_active
from app.cache.redis import redis_client
from app.db.session import SessionLocal
from app.events import user_channel

router = APIRouter(tags=["stream"])

PING_SECONDS = 15
SESSION_CHECK_SECONDS = 60
SUBSCRIBE_CONFIRM_ATTEMPTS = 6  # x 0.5 s


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _session_still_active(user: CurrentUser) -> bool:
    async with SessionLocal() as db:
        return await session_is_active(db, redis_client, user.session_id, user.user_id)


async def event_stream(
    user: CurrentUser,
    *,
    redis: Redis,
    is_disconnected: Callable[[], Awaitable[bool]],
    session_active: Callable[[], Awaitable[bool]],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ping_seconds: float = PING_SECONDS,
) -> AsyncIterator[str]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(user_channel(user.user_id))
    # Wait for Redis to confirm the subscription, so "ready" really means no event published
    # from now on can be missed. (Until confirmed, a PUBLISH from another connection may
    # reach Redis first and find no subscriber.)
    for _ in range(SUBSCRIBE_CONFIRM_ATTEMPTS):
        message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=0.5)
        if message is not None and message["type"] == "subscribe":
            break
    last_session_check = now()
    try:
        yield "retry: 5000\n\n"
        yield sse("ready", {})
        while True:
            if await is_disconnected():
                return
            current = now()
            if current >= user.token_expires_at:
                yield sse("reauth", {"reason": "token_expired"})
                return
            if (current - last_session_check).total_seconds() >= SESSION_CHECK_SECONDS:
                last_session_check = current
                if not await session_active():
                    yield sse("reauth", {"reason": "session_ended"})
                    return
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=ping_seconds)
            if message is None:
                yield ": ping\n\n"
                continue
            try:
                payload = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            yield sse(payload.pop("type", "message"), payload)
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@router.get("/stream", response_class=StreamingResponse)
async def stream(request: Request, token: BearerToken) -> StreamingResponse:
    # Authenticate with a short-lived DB session: a stream can stay open for minutes and
    # must not hold a pooled connection that long.
    async with SessionLocal() as db:
        user = await authenticate(db, token)

    async def session_active() -> bool:
        return await _session_still_active(user)

    return StreamingResponse(
        event_stream(
            user,
            redis=redis_client,
            is_disconnected=request.is_disconnected,
            session_active=session_active,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
