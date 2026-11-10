import logging
import re
import sys
import uuid
from contextvars import ContextVar

# The id of the request being served on this task, so every line logged while handling it
# can be tied together. A context variable (not a global) because the API serves many
# requests concurrently on one event loop. Workers leave it at "-".
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Ids that reach us from outside are echoed into every log line, so they are restricted to
# a short, boring alphabet: a newline or control character in a header must not be able to
# forge log entries.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def clean_request_id(value: str | None) -> str:
    """An inbound X-Request-ID if it is safe to log, otherwise a fresh one."""
    if value and _SAFE_ID.match(value):
        return value
    return new_request_id()


class _RequestIdFilter(logging.Filter):
    """Puts request_id on every record, so the formatter can always reference it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str) -> None:
    """Plain, single-line logs on stdout so `docker compose logs` stays readable."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s")
    )
    # On the handler rather than a logger: it then applies to every record that reaches
    # this handler, whichever logger produced it, before the formatter reads request_id.
    handler.addFilter(_RequestIdFilter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
