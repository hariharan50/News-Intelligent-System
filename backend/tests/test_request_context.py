"""Request ids and the catch-all error handler (no database or Redis needed)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# A route that fails the way an unforeseen bug would. Registered on the real app so the
# test exercises the real middleware and exception handler rather than a stand-in.
_BOOM = "/api/_test/boom"


@app.get(_BOOM, include_in_schema=False)
async def _boom() -> None:
    raise RuntimeError("something nobody anticipated")


@pytest.fixture
async def client():
    # raise_app_exceptions=False: Starlette re-raises after the handler has produced the
    # response, so without this the test would see the exception instead of the 500.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_every_response_carries_a_request_id(client) -> None:
    resp = await client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.headers["x-request-id"]


async def test_a_safe_inbound_request_id_is_kept(client) -> None:
    """So a trace started at the proxy continues through our logs."""
    resp = await client.get("/api/health/live", headers={"X-Request-ID": "edge-abc_123.4"})
    assert resp.headers["x-request-id"] == "edge-abc_123.4"


@pytest.mark.parametrize(
    "hostile",
    [
        "has space",
        "x" * 65,  # too long
        "semi;colon",
        "",
    ],
)
async def test_an_unsafe_inbound_request_id_is_replaced(client, hostile: str) -> None:
    """A header must not be able to write arbitrary text into our log lines."""
    resp = await client.get("/api/health/live", headers={"X-Request-ID": hostile})
    assert resp.headers["x-request-id"] != hostile
    assert resp.headers["x-request-id"].isalnum()


async def test_each_request_gets_its_own_id(client) -> None:
    first = (await client.get("/api/health/live")).headers["x-request-id"]
    second = (await client.get("/api/health/live")).headers["x-request-id"]
    assert first != second


async def test_an_unhandled_error_keeps_the_api_error_shape(client) -> None:
    """Without the catch-all handler this is plain text, and the browser can only say
    "Request failed (500)" because there is no code or message to read."""
    resp = await client.get(_BOOM)
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["code"] == "server_error"
    assert detail["message"]
    # The id in the body matches the header, so a person can quote it from either.
    assert detail["request_id"] == resp.headers["x-request-id"]


async def test_an_unhandled_error_does_not_leak_the_cause(client) -> None:
    body = (await client.get(_BOOM)).text
    assert "something nobody anticipated" not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
