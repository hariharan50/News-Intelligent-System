import asyncio
import logging
import time
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.cache import redis as cache
from app.core.config import get_settings
from app.db.session import ping_database

router = APIRouter(prefix="/health", tags=["health"])
log = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0

CheckStatus = Literal["ok", "error"]


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class PipelineStatus(BaseModel):
    status: Literal["ok", "stale", "missing"]
    last_heartbeat_age_seconds: float | None = None


class ReadyResponse(BaseModel):
    status: Literal["ok", "error"]
    database: CheckStatus
    redis: CheckStatus
    pipeline: PipelineStatus


async def _check(name: str, probe) -> CheckStatus:
    try:
        await asyncio.wait_for(probe(), timeout=CHECK_TIMEOUT_SECONDS)
        return "ok"
    except Exception:
        log.warning("readiness check failed: %s", name, exc_info=True)
        return "error"


async def _pipeline_status() -> PipelineStatus:
    """The scheduler enqueues a heartbeat task every minute and a worker records it.

    A fresh heartbeat proves scheduler -> Redis -> worker is flowing end to end.
    It is reported but does not fail readiness: the API can serve while workers restart.
    """
    try:
        raw = await asyncio.wait_for(
            cache.redis_client.get(cache.HEARTBEAT_KEY), CHECK_TIMEOUT_SECONDS
        )
    except Exception:
        return PipelineStatus(status="missing")
    if raw is None:
        return PipelineStatus(status="missing")
    age = max(0.0, time.time() - float(raw))
    stale = age > get_settings().heartbeat_max_age_seconds
    return PipelineStatus(
        status="stale" if stale else "ok", last_heartbeat_age_seconds=round(age, 1)
    )


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    """The process is up. Used by the container healthcheck."""
    return LiveResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready(response: Response) -> ReadyResponse:
    """Dependencies are reachable. Returns 503 if PostgreSQL or Redis is down."""
    database, redis_status, pipeline = await asyncio.gather(
        _check("database", ping_database),
        _check("redis", cache.ping_redis),
        _pipeline_status(),
    )
    healthy = database == "ok" and redis_status == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status="ok" if healthy else "error",
        database=database,
        redis=redis_status,
        pipeline=pipeline,
    )
