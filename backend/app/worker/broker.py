from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import RedisStreamBroker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.monitoring import init_monitoring
from app.ingestion.http import close_client

settings = get_settings()

# Redis Streams give at-least-once delivery: a task a crashed worker never acknowledged
# is picked up again by another worker, so pipeline tasks must be idempotent.
broker = RedisStreamBroker(
    url=settings.redis_url, queue_name="nis:tasks", consumer_group_name="nis"
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_worker_startup(_: TaskiqState) -> None:
    configure_logging(settings.log_level)
    init_monitoring("worker")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(_: TaskiqState) -> None:
    await close_client()
