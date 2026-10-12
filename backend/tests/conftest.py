"""Test configuration.

Unit tests need nothing running. Tests that use the `db` fixture need the Docker PostgreSQL
and Redis (`docker compose up -d postgres redis`); they use a separate `nis_test` database and
Redis DB 15, and are skipped if those can't be reached.

Connection details come from TEST_DATABASE_URL / TEST_REDIS_URL, else from the repo's .env.
"""

import asyncio
import itertools
import os
from pathlib import Path

from dotenv import dotenv_values

_ROOT_ENV = dotenv_values(Path(__file__).resolve().parents[2] / ".env")


def _test_database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL"):
        return url
    user = _ROOT_ENV.get("POSTGRES_USER") or "nis"
    password = _ROOT_ENV.get("POSTGRES_PASSWORD") or "nis"
    port = _ROOT_ENV.get("POSTGRES_HOST_PORT") or "5432"
    return f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/nis_test"


# Must happen before anything imports app settings. 127.0.0.1, not localhost: on Windows
# localhost tries IPv6 first and every new connection stalls ~2 s before falling back.
os.environ["ENVIRONMENT"] = "test"
# The paid and AI features are tested with their switches on; test_features covers "off".
os.environ["FEATURE_BILLING"] = "true"
os.environ["FEATURE_AI"] = "true"
os.environ["DATABASE_URL"] = _test_database_url()
os.environ["REDIS_URL"] = os.environ.get(
    "TEST_REDIS_URL", f"redis://127.0.0.1:{_ROOT_ENV.get('REDIS_HOST_PORT') or '6379'}/15"
)

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from alembic import command  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


async def _ensure_database(url: str) -> None:
    sa_url = make_url(url)
    conn = await asyncpg.connect(
        user=sa_url.username,
        password=sa_url.password,
        host=sa_url.host,
        port=sa_url.port,
        database="postgres",
        timeout=3,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", sa_url.database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{sa_url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Create nis_test if needed and migrate it to head, once per test run."""
    try:
        asyncio.run(_ensure_database(os.environ["DATABASE_URL"]))
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        pytest.skip(f"PostgreSQL not reachable for integration tests: {exc}")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
async def db(migrated_database):
    """Empty tables and a clean Redis DB for each integration test. Yields the session factory."""
    from app.cache.redis import redis_client
    from app.db.session import SessionLocal, engine

    try:
        await redis_client.flushdb()
    except Exception as exc:
        pytest.skip(f"Redis not reachable for integration tests: {exc}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE sources, articles, source_fetches, stories, users, ai_usage_daily, "
                "billing_events "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield SessionLocal


TEST_PASSWORD = "correct horse battery 42"
_phone_counter = itertools.count(1)


def signup_body(email: str = "trader@example.com", **overrides) -> dict:
    """A valid sign-up request. Each call gets its own phone number (phones are unique)."""
    body = {
        "full_name": "Asha Rao",
        "phone": f"+919{876500000 + next(_phone_counter):09d}",
        "email": email,
        "password": TEST_PASSWORD,
        "confirm_password": TEST_PASSWORD,
        "accept_terms": True,
        "accept_risk_notice": True,
    }
    return body | overrides


@pytest.fixture
async def api(db):
    """An HTTP client for the app. Each client has its own cookie jar (one 'browser')."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    clients: list[AsyncClient] = []

    def new_client() -> AsyncClient:
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield new_client
    for client in clients:
        await client.aclose()
