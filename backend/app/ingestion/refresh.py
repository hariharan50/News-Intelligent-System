"""'Refresh now': poll every healthy source right away instead of waiting for its turn.

The API never fetches anything itself. It marks sources as due, and the scheduler's
dispatch_due (every few seconds) hands them to the worker as usual, so host spacing,
ETags, timeouts, the per-source lock and failure back-off all still apply. One run is
shared by everyone: a click during a run (or soon after) joins it, so many clicks never
turn into many requests to publishers.
"""

import json
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RUN_KEY = "nis:refresh:run"
MIN_GAP_SECONDS = 90  # a new run can start this long after the previous one started
RUN_TIMEOUT_SECONDS = 75  # after this a run counts as finished even if a source hangs
FRESH_SECONDS = 60  # sources polled this recently are skipped: nothing new to find


async def status(db: AsyncSession, redis: Redis) -> dict:
    """{running, total, done, started_at, next_allowed_in}."""
    raw = await redis.get(RUN_KEY)
    if not raw:
        return {"running": False, "total": 0, "done": 0, "started_at": None, "next_allowed_in": 0}
    run = json.loads(raw)
    started = datetime.fromisoformat(run["started_at"])
    total = len(run["ids"])
    done = 0
    if total:
        done = await db.scalar(
            text(
                "SELECT count(*) FROM sources "
                "WHERE id = ANY(CAST(:ids AS uuid[])) AND last_polled_at >= :started"
            ),
            {"ids": run["ids"], "started": started},
        )
    age = (datetime.now(UTC) - started).total_seconds()
    return {
        "running": done < total and age < RUN_TIMEOUT_SECONDS,
        "total": total,
        "done": min(done, total),
        "started_at": run["started_at"],
        "next_allowed_in": max(await redis.ttl(RUN_KEY), 0),
    }


async def start(db: AsyncSession, redis: Redis) -> dict:
    """Start a run, or join the one in progress (or just finished)."""
    if not await redis.exists(RUN_KEY):
        started = datetime.now(UTC)
        # Healthy sources only: a failing one keeps its back-off, so a click can't make
        # us hammer a site that is already refusing us.
        ids = (
            await db.execute(
                text(
                    """
                    UPDATE sources SET next_poll_at = LEAST(next_poll_at, now())
                     WHERE enabled AND consecutive_failures = 0
                       AND (last_polled_at IS NULL
                            OR last_polled_at < now() - make_interval(secs => :fresh))
                     RETURNING id
                    """
                ),
                {"fresh": FRESH_SECONDS},
            )
        ).scalars()
        run = {"started_at": started.isoformat(), "ids": [str(i) for i in ids]}
        # NX: if two clicks race, one run is recorded; the other's UPDATE is rolled back.
        if await redis.set(RUN_KEY, json.dumps(run), nx=True, ex=MIN_GAP_SECONDS):
            await db.commit()
        else:
            await db.rollback()
    return await status(db, redis)
