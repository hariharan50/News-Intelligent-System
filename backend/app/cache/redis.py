from redis.asyncio import Redis

from app.core.config import get_settings

redis_client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)

# Redis keys are namespaced under "nis:" so the instance can be shared safely.
HEARTBEAT_KEY = "nis:heartbeat:pipeline"


async def ping_redis() -> None:
    await redis_client.ping()
