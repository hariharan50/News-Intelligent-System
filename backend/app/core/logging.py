import logging
import sys


def configure_logging(level: str) -> None:
    """Plain, single-line logs on stdout so `docker compose logs` stays readable."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
