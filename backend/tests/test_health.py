import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import health
from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("down")


class FakeRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, _key: str) -> str | None:
        return self.value


async def test_live(client: AsyncClient) -> None:
    resp = await client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_all_ok(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "ping_database", _ok)
    monkeypatch.setattr(health.cache, "ping_redis", _ok)
    monkeypatch.setattr(health.cache, "redis_client", FakeRedis(str(time.time() - 5)))

    resp = await client.get("/api/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
    assert body["pipeline"]["status"] == "ok"


async def test_ready_database_down_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "ping_database", _fail)
    monkeypatch.setattr(health.cache, "ping_redis", _ok)
    monkeypatch.setattr(health.cache, "redis_client", FakeRedis(None))

    resp = await client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["database"] == "error"
    assert resp.json()["pipeline"]["status"] == "missing"


async def test_stale_heartbeat_does_not_fail_readiness(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "ping_database", _ok)
    monkeypatch.setattr(health.cache, "ping_redis", _ok)
    monkeypatch.setattr(health.cache, "redis_client", FakeRedis(str(time.time() - 3600)))

    resp = await client.get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json()["pipeline"]["status"] == "stale"
