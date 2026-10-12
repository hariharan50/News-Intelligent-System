"""'Refresh now': marks healthy sources due; runs are shared and spaced out."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.models import Source
from tests.test_profiles_feed_api import add_source, sign_up


async def _set(db, source_id, **values) -> None:
    async with db() as session, session.begin():
        await session.execute(update(Source).where(Source.id == source_id).values(**values))


async def _next_poll(db, source_id) -> datetime:
    async with db() as session:
        return (await session.get(Source, source_id)).next_poll_at


async def test_refresh_marks_healthy_sources_due_and_reports_progress(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=10)
    healthy = await add_source(db, "et", "https://et.example")
    failing = await add_source(db, "bs", "https://bs.example")
    fresh = await add_source(db, "mint", "https://mint.example")
    off = await add_source(db, "old", "https://old.example", enabled=False)
    await _set(db, healthy, next_poll_at=later, last_polled_at=now - timedelta(minutes=5))
    await _set(db, failing, next_poll_at=later, consecutive_failures=3)  # keeps its back-off
    await _set(db, fresh, next_poll_at=later, last_polled_at=now - timedelta(seconds=10))
    await _set(db, off, next_poll_at=later)

    resp = await client.post("/api/feed/refresh", headers=headers)
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["running"] is True and run["total"] == 1 and run["done"] == 0
    assert await _next_poll(db, healthy) <= datetime.now(UTC)
    for untouched in (failing, fresh, off):
        assert await _next_poll(db, untouched) > datetime.now(UTC)

    # The worker polls it: progress completes.
    await _set(db, healthy, last_polled_at=datetime.now(UTC) + timedelta(seconds=1))
    done = (await client.get("/api/feed/refresh", headers=headers)).json()
    assert done["running"] is False and done["done"] == 1 and done["next_allowed_in"] > 0


async def test_clicks_join_the_current_run(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    first_source = await add_source(db, "et", "https://et.example")
    await _set(db, first_source, next_poll_at=datetime.now(UTC) + timedelta(minutes=10))
    first = (await client.post("/api/feed/refresh", headers=headers)).json()

    # A source added afterwards is not swept up by a second click within the gap.
    late = await add_source(db, "mint", "https://mint.example")
    await _set(db, late, next_poll_at=datetime.now(UTC) + timedelta(minutes=10))
    second = (await client.post("/api/feed/refresh", headers=headers)).json()
    assert second["started_at"] == first["started_at"] and second["total"] == 1
    assert await _next_poll(db, late) > datetime.now(UTC)


async def test_refresh_needs_sign_in_and_is_rate_limited(api, db) -> None:
    client = api()
    assert (await client.post("/api/feed/refresh")).status_code == 401
    headers = await sign_up(client)
    codes = [(await client.post("/api/feed/refresh", headers=headers)).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
