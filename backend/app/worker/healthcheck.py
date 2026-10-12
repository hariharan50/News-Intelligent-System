"""Container healthcheck for the worker: exit 0 if a pipeline heartbeat is fresh.

Usage: python -m app.worker.healthcheck
"""

import sys
import time

from redis import Redis

from app.cache.redis import HEARTBEAT_KEY
from app.core.config import get_settings


def main() -> int:
    settings = get_settings()
    try:
        raw = Redis.from_url(settings.redis_url, socket_timeout=2).get(HEARTBEAT_KEY)
    except Exception as exc:
        print(f"redis unreachable: {exc}")
        return 1
    if raw is None:
        print("no heartbeat yet")
        return 1
    age = time.time() - float(raw)
    if age > settings.heartbeat_max_age_seconds:
        print(f"heartbeat stale: {age:.0f}s old")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
