"""Live events for connected browsers, over Redis pub/sub (one channel per user).

Pub/sub is fire-and-forget: a browser that is offline simply misses events and reloads its
feed when it reconnects. Nothing here is a source of truth.
"""

import json
import logging
import uuid

from redis.asyncio import Redis

log = logging.getLogger(__name__)


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"nis:events:user:{user_id}"


async def publish_to_user(redis: Redis, user_id: uuid.UUID, event: dict) -> None:
    try:
        await redis.publish(user_channel(user_id), json.dumps(event, default=str))
    except Exception:  # live updates are best-effort; never fail the pipeline for them
        log.warning("could not publish event to user %s", user_id, exc_info=True)
