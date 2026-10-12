"""Error monitoring with Sentry (optional: does nothing without SENTRY_DSN).

Unhandled exceptions in the API, worker tasks and the Telegram poller are reported with a
stack trace. No personal data is sent: no request bodies, cookies, headers or user emails.
"""

import logging
import os

from app.core.config import get_settings

log = logging.getLogger(__name__)
_started = False


def _scrub(event: dict, _hint: dict) -> dict:
    request = event.get("request") or {}
    for key in ("cookies", "data", "headers", "env"):
        request.pop(key, None)
    user = event.get("user")
    if user:
        event["user"] = {"id": user.get("id")}
    return event


def init_monitoring(component: str) -> bool:
    """Start Sentry for this process. Returns True if enabled."""
    global _started
    s = get_settings()
    if _started or not s.sentry_dsn:
        return _started
    import sentry_sdk

    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.environment,
        release=os.environ.get("APP_VERSION") or None,
        server_name=component,
        traces_sample_rate=s.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_scrub,
    )
    sentry_sdk.set_tag("component", component)
    _started = True
    log.info("error monitoring on (Sentry) for %s", component)
    return True
