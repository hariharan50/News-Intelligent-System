"""Read/saved state, search, live events and the SSE stream (needs Docker PostgreSQL/Redis)."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.stream import event_stream
from app.auth.deps import CurrentUser
from app.cache.redis import redis_client
from app.events import user_channel
from tests.test_profiles_feed_api import add_article, add_source, pipeline, sign_up

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def feed_news(db) -> None:
    rbi = await add_source(
        db, "rbi-press-releases", "https://www.rbi.org.in", name="RBI Press Releases",
        category="official_india", licence_class="official", authority=5,
    )  # fmt: skip
    et = await add_source(db, "et-markets", "https://economictimes.indiatimes.com", name="ET")
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 30
    )
    await add_article(db, et, "Brent crude jumps 4% after supply disruption in the Red Sea", 40)
    await add_article(db, et, "Rupee hits record low as RBI steps in to support currency", 20)
    await pipeline(db)


async def test_read_state_unread_filter_and_mark_all(api, feed_news) -> None:
    client = api()
    headers = await sign_up(client)
    feed = (await client.get("/api/feed", headers=headers)).json()
    assert feed["unread"] == feed["total"] >= 3
    first = feed["items"][0]["story_id"]

    assert (await client.put(f"/api/stories/{first}/read", headers=headers)).status_code == 204
    after = (await client.get("/api/feed", headers=headers)).json()
    assert after["unread"] == feed["total"] - 1
    assert next(i for i in after["items"] if i["story_id"] == first)["is_read"]

    unread = (await client.get("/api/feed?unread_only=true", headers=headers)).json()
    assert first not in {i["story_id"] for i in unread["items"]}
    assert unread["total"] == after["unread"]

    marked = await client.post("/api/feed/mark-read", headers=headers, json={"since_hours": 24})
    assert marked.json()["marked"] == feed["total"]
    assert (await client.get("/api/feed", headers=headers)).json()["unread"] == 0

    assert (await client.delete(f"/api/stories/{first}/read", headers=headers)).status_code == 204
    assert (await client.get("/api/feed", headers=headers)).json()["unread"] == 1


async def test_saved_stories(api, feed_news) -> None:
    client = api()
    headers = await sign_up(client)
    items = (await client.get("/api/feed", headers=headers)).json()["items"]
    a, b = items[0]["story_id"], items[1]["story_id"]

    await client.put(f"/api/stories/{a}/saved", headers=headers)
    await client.put(f"/api/stories/{b}/saved", headers=headers)
    saved = (await client.get("/api/saved", headers=headers)).json()
    assert [i["story_id"] for i in saved["items"]] == [b, a]  # newest saved first
    assert all(i["is_saved"] and i["score"] for i in saved["items"])

    await client.delete(f"/api/stories/{b}/saved", headers=headers)
    assert (await client.get("/api/saved", headers=headers)).json()["total"] == 1

    missing = await client.put(f"/api/stories/{uuid.uuid4()}/saved", headers=headers)
    assert missing.status_code == 404


async def test_state_is_private_per_user(api, feed_news) -> None:
    alice, bob = api(), api()
    alice_headers = await sign_up(alice, "alice@example.com")
    bob_headers = await sign_up(bob, "bob@example.com")
    story = (await alice.get("/api/feed", headers=alice_headers)).json()["items"][0]["story_id"]
    await alice.put(f"/api/stories/{story}/saved", headers=alice_headers)
    await alice.put(f"/api/stories/{story}/read", headers=alice_headers)

    assert (await bob.get("/api/saved", headers=bob_headers)).json()["total"] == 0
    bob_item = next(
        i for i in (await bob.get("/api/feed", headers=bob_headers)).json()["items"]
        if i["story_id"] == story
    )  # fmt: skip
    assert not bob_item["is_read"] and not bob_item["is_saved"]


async def test_search(api, feed_news) -> None:
    client = api()
    headers = await sign_up(client)
    crude = (await client.get("/api/feed?q=Brent", headers=headers)).json()
    assert [i["title"] for i in crude["items"]] == [
        "Brent crude jumps 4% after supply disruption in the Red Sea"
    ]
    # LIKE wildcards in the query are literal, not patterns.
    assert (await client.get("/api/feed?q=%25%25", headers=headers)).json()["total"] == 0


# --------------------------------------------------------------------------- live events


async def _collect_events(user_id: uuid.UUID, during) -> list[str]:
    """Subscribe like a browser would, run `during`, and return the published payloads."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(user_channel(user_id))
    try:
        await during()
        # get_message() also returns None for the (ignored) subscribe confirmation, so poll
        # for a fixed window rather than stopping at the first None.
        messages = []
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m is not None:
                messages.append(json.loads(m["data"]))
        return messages
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


async def test_new_story_is_announced_once(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = uuid.UUID((await client.get("/api/auth/me", headers=headers)).json()["id"])
    rbi = await add_source(
        db, "rbi-press-releases", "https://www.rbi.org.in", category="official_india",
        licence_class="official", authority=5,
    )  # fmt: skip

    async def arrive() -> None:
        await add_article(db, rbi, "RBI cuts repo rate by 25 bps to 5.25%", 1)
        await pipeline(db)

    events = await _collect_events(user_id, arrive)
    # One "story" event for the feed, and (critical, pinned) one "alert" event.
    assert sorted(e["type"] for e in events) == ["alert", "story"]
    events = [e for e in events if e["type"] == "story"]
    assert events[0] | {"story_id": None, "profile_id": None} == {
        "type": "story", "score": 95, "tier": "critical", "alert": True, "new": True,
        "story_id": None, "profile_id": None, "title": "RBI cuts repo rate by 25 bps to 5.25%",
    }  # fmt: skip

    # Re-scoring the same story changes nothing and announces nothing.
    from app.scoring.service import score_unscored

    async def rescore_again() -> None:
        from sqlalchemy import update

        from app.models import Story

        async with db() as session, session.begin():
            await session.execute(update(Story).values(scored_at=None))
        await score_unscored(db)

    assert await _collect_events(user_id, rescore_again) == []


async def test_old_and_irrelevant_stories_are_not_announced(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = uuid.UUID((await client.get("/api/auth/me", headers=headers)).json()["id"])
    bbc = await add_source(db, "bbc-world", "https://www.bbc.com", region="GLOBAL")
    rbi = await add_source(db, "rbi-press-releases", "https://www.rbi.org.in", authority=5)

    async def arrive() -> None:
        await add_article(db, bbc, "Powerful earthquake hits Panama", 1)  # irrelevant
        await add_article(db, rbi, "RBI cuts repo rate by 25 bps", 60 * 30)  # 30 hours old
        await pipeline(db)

    assert await _collect_events(user_id, arrive) == []


async def test_event_stream_relays_events_and_ends_at_token_expiry() -> None:
    # Long enough for the published message to be read first, even on a slow machine.
    user = CurrentUser(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC) + timedelta(seconds=5))

    async def never_disconnected() -> bool:
        return False

    async def active() -> bool:
        return True

    stream = event_stream(
        user, redis=redis_client, is_disconnected=never_disconnected,
        session_active=active, ping_seconds=0.2,
    )  # fmt: skip
    assert await anext(stream) == "retry: 5000\n\n"
    assert await anext(stream) == "event: ready\ndata: {}\n\n"

    # "ready" promises the subscription is live: publishing right away must reach it.
    receivers = await redis_client.publish(
        user_channel(user.user_id), json.dumps({"type": "story", "score": 91})
    )
    assert receivers == 1  # the stream's subscription is live
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
        if chunk.startswith("event: reauth"):
            break
    assert 'event: story\ndata: {"score": 91}\n\n' in chunks
    assert any(c == ": ping\n\n" for c in chunks)
    assert chunks[-1].startswith("event: reauth") and "token_expired" in chunks[-1]


async def test_event_stream_ends_when_session_is_revoked(monkeypatch) -> None:
    import app.api.stream as stream_module

    monkeypatch.setattr(stream_module, "SESSION_CHECK_SECONDS", 0)
    user = CurrentUser(uuid.uuid4(), uuid.uuid4(), datetime.now(UTC) + timedelta(hours=1))

    async def never_disconnected() -> bool:
        return False

    async def revoked() -> bool:
        return False

    stream = event_stream(
        user, redis=redis_client, is_disconnected=never_disconnected,
        session_active=revoked, ping_seconds=0.1,
    )  # fmt: skip
    chunks = [chunk async for chunk in stream]
    assert chunks[-1].startswith("event: reauth") and "session_ended" in chunks[-1]


async def test_stream_endpoint_requires_auth(api) -> None:
    assert (await api().get("/api/stream")).status_code == 401
    await asyncio.sleep(0)
