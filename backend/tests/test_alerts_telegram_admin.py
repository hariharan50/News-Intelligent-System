"""Alerts, Telegram, feedback and admin (needs Docker PostgreSQL/Redis)."""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, update

from app.alerts.telegram import deliver_pending_telegram, format_alert
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.net import BlockedAddressError, assert_public_host
from app.models import Alert, DeliveryStatus, TelegramLink, User
from app.telegram.poller import handle_update
from tests.test_feed_state_and_stream import _collect_events
from tests.test_profiles_feed_api import add_article, add_source, pipeline, sign_up

pytestmark = pytest.mark.usefixtures("db")

CHAT_ID = 424242


@pytest.fixture
def telegram_on(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", "123:TEST")
    monkeypatch.setattr(settings, "telegram_bot_username", "NisTestBot")


class FakeTelegram:
    """Stands in for TelegramClient in the poller: records what the bot would say."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def telegram_api(responses: list[dict] | None = None):
    """A mock Telegram HTTP API. Returns (client, calls)."""
    calls: list[dict] = []
    queue = list(responses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"method": request.url.path.rsplit("/", 1)[-1], **body})
        reply = queue.pop(0) if queue else {"ok": True, "result": {"message_id": 1}}
        return httpx.Response(200 if reply.get("ok") else reply.get("error_code", 400), json=reply)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def user_id_of(client, headers) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/auth/me", headers=headers)).json()["id"])


async def rbi_source(db):
    return await add_source(
        db, "rbi-press-releases", "https://www.rbi.org.in", name="RBI Press Releases",
        category="official_india", licence_class="official", authority=5,
    )  # fmt: skip


async def alerts_of(db, user_id) -> list[Alert]:
    async with db() as session:
        return list(
            await session.scalars(
                select(Alert).where(Alert.user_id == user_id).order_by(Alert.created_at)
            )
        )


async def connect_telegram(api, db, client, headers) -> None:
    link = (await client.post("/api/notifications/telegram/link", headers=headers)).json()
    token = link["url"].split("start=", 1)[1]
    fake = FakeTelegram()
    await handle_update(
        {"update_id": 1, "message": {"text": f"/start {token}", "chat": {"id": CHAT_ID, "type": "private", "username": "asha"}}},
        session_factory=db, redis=redis_client, tg=fake,
    )  # fmt: skip
    assert "Connected" in fake.sent[0][1]


# --------------------------------------------------------------------------- alerts


async def test_one_alert_per_event_and_inbox(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    rbi = await rbi_source(db)
    et = await add_source(db, "et-economy", "https://economictimes.indiatimes.com", name="ET")
    mint = await add_source(db, "mint-economy", "https://www.livemint.com", name="Mint")

    await add_article(db, et, "RBI steps in to support rupee as currency nears record low", 30)
    await pipeline(db)
    await add_article(
        db, mint, "Reserve Bank of India intervenes in forex market to support rupee", 20
    )
    await pipeline(db)
    assert len(await alerts_of(db, user_id)) == 1  # same event (currency intervention, RBI)

    await add_article(db, rbi, "RBI cuts repo rate by 25 bps to 5.25%", 5)
    await pipeline(db)
    alerts = await alerts_of(db, user_id)
    assert len(alerts) == 2
    assert alerts[1].pinned and alerts[1].score == 95
    assert alerts[1].telegram_status == DeliveryStatus.SKIPPED  # Telegram not connected

    inbox = (await client.get("/api/alerts", headers=headers)).json()
    assert (inbox["total"], inbox["unread"]) == (2, 2)
    assert inbox["items"][0]["title"] == "RBI cuts repo rate by 25 bps to 5.25%"
    assert (await client.get("/api/alerts/unread-count", headers=headers)).json() == {"unread": 2}
    await client.post("/api/alerts/read-all", headers=headers)
    assert (await client.get("/api/alerts/unread-count", headers=headers)).json() == {"unread": 0}


async def test_old_or_low_scoring_stories_do_not_alert(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI cuts repo rate by 25 bps", 60 * 30)  # 30 hours old
    await add_article(db, rbi, "Auction of 91-Day Treasury Bills", 5)  # routine: below 80
    await pipeline(db)
    assert await alerts_of(db, user_id) == []


async def test_alert_is_pushed_live(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    rbi = await rbi_source(db)

    async def arrive() -> None:
        await add_article(db, rbi, "RBI cuts repo rate by 25 bps to 5.25%", 1)
        await pipeline(db)

    events = await _collect_events(user_id, arrive)
    alert_events = [e for e in events if e["type"] == "alert"]
    assert len(alert_events) == 1
    assert alert_events[0]["score"] == 95 and alert_events[0]["pinned"] is True


# --------------------------------------------------------------------------- telegram


async def test_telegram_not_configured(api) -> None:
    client = api()
    headers = await sign_up(client)
    status = (await client.get("/api/notifications", headers=headers)).json()
    assert status["telegram"] == {
        "configured": False, "bot_username": None, "connected": False,
        "username": None, "first_name": None, "linked_at": None,
    }  # fmt: skip
    assert (
        await client.post("/api/notifications/telegram/link", headers=headers)
    ).status_code == 503


async def test_telegram_connect_deliver_and_disconnect(api, db, telegram_on) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    await connect_telegram(api, db, client, headers)

    status = (await client.get("/api/notifications", headers=headers)).json()["telegram"]
    assert status["connected"] and status["username"] == "asha"

    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI cuts repo rate by 25 bps & keeps <stance> neutral", 1)
    await pipeline(db)
    [alert] = await alerts_of(db, user_id)
    assert alert.telegram_status == DeliveryStatus.PENDING

    http, calls = telegram_api()
    async with http:
        assert await deliver_pending_telegram(db, redis=redis_client, http=http) == 1
    [call] = calls
    assert call["method"] == "sendMessage" and call["chat_id"] == CHAT_ID
    assert "&amp; keeps &lt;stance&gt; neutral" in call["text"]  # HTML-escaped
    assert "📌 Pinned" in call["text"]
    [alert] = await alerts_of(db, user_id)
    assert alert.telegram_status == DeliveryStatus.SENT and alert.telegram_sent_at

    # Nothing left to send.
    http, calls = telegram_api()
    async with http:
        assert await deliver_pending_telegram(db, redis=redis_client, http=http) == 0

    assert (await client.delete("/api/notifications/telegram", headers=headers)).status_code == 204
    assert not (await client.get("/api/notifications", headers=headers)).json()["telegram"][
        "connected"
    ]


async def test_telegram_rate_limit_is_retried(api, db, telegram_on) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    await connect_telegram(api, db, client, headers)
    await add_article(db, await rbi_source(db), "RBI cuts repo rate by 25 bps", 1)
    await pipeline(db)

    http, calls = telegram_api([
        {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 1}},
    ])  # fmt: skip
    async with http:
        await deliver_pending_telegram(db, redis=redis_client, http=http)
    assert len(calls) == 2
    [alert] = await alerts_of(db, user_id)
    assert alert.telegram_status == DeliveryStatus.SENT


async def test_blocked_bot_disconnects_the_chat(api, db, telegram_on) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    await connect_telegram(api, db, client, headers)
    await add_article(db, await rbi_source(db), "RBI cuts repo rate by 25 bps", 1)
    await pipeline(db)

    http, _ = telegram_api([
        {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"},
    ])  # fmt: skip
    async with http:
        await deliver_pending_telegram(db, redis=redis_client, http=http)
    [alert] = await alerts_of(db, user_id)
    assert alert.telegram_status == DeliveryStatus.FAILED
    async with db() as session:
        assert await session.get(TelegramLink, user_id) is None


async def test_quiet_hours_hold_back_unpinned_alerts(api, db, telegram_on) -> None:
    client = api()
    headers = await sign_up(client)
    user_id = await user_id_of(client, headers)
    await connect_telegram(api, db, client, headers)
    now = datetime.now(UTC)
    minute = now.hour * 60 + now.minute
    resp = await client.put(
        "/api/notifications", headers=headers,
        json={"telegram_enabled": True, "quiet_start": (minute - 30) % 1440, "quiet_end": (minute + 30) % 1440, "timezone": "UTC"},
    )  # fmt: skip
    assert resp.status_code == 200

    et = await add_source(db, "et-economy", "https://economictimes.indiatimes.com", name="ET")
    await add_article(db, et, "RBI steps in to support rupee as currency nears record low", 2)
    await add_article(db, await rbi_source(db), "RBI cuts repo rate by 25 bps", 1)
    await pipeline(db)
    statuses = {a.pinned: a.telegram_status for a in await alerts_of(db, user_id)}
    assert statuses == {False: DeliveryStatus.QUIET, True: DeliveryStatus.PENDING}


async def test_expired_or_reused_link_is_refused(api, db, telegram_on) -> None:
    client = api()
    headers = await sign_up(client)
    link = (await client.post("/api/notifications/telegram/link", headers=headers)).json()
    token = link["url"].split("start=", 1)[1]
    fake = FakeTelegram()
    message = {"text": f"/start {token}", "chat": {"id": CHAT_ID, "type": "private"}}
    await handle_update(
        {"update_id": 1, "message": message}, session_factory=db, redis=redis_client, tg=fake
    )
    await handle_update(
        {"update_id": 2, "message": message}, session_factory=db, redis=redis_client, tg=fake
    )
    assert "Connected" in fake.sent[0][1]
    assert "expired" in fake.sent[1][1]


def test_format_alert_escapes_and_explains() -> None:
    alert = Alert(
        title="Fed <cuts> & holds", url='https://x.example/?a=1&b="2"', source_name="Fed",
        score=82, pinned=False,
        reasons=[{"kind": "entity", "label": "Entity: US Federal Reserve"}, {"kind": "importance", "label": "Official source"}],
    )  # fmt: skip
    text = format_alert(alert)
    assert text.startswith("🔴 <b>CRITICAL 82</b>")
    assert "Fed &lt;cuts&gt; &amp; holds" in text
    assert "Entity: US Federal Reserve" in text and "Official source" not in text


# --------------------------------------------------------------------------- feedback


async def test_feedback_adjusts_weights_suggests_mute_and_resets(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    profile_id = (await client.get("/api/profiles", headers=headers)).json()[0]["id"]
    et = await add_source(db, "et-markets", "https://economictimes.indiatimes.com", name="ET")
    for i, title in enumerate([
        "Reliance Industries shares rise 2% on retail expansion",
        "Reliance Industries plans new energy gigafactory",
        "Reliance Industries board meets on Jio listing",
    ]):  # fmt: skip
        await add_article(db, et, title, 10 + i)
    await pipeline(db)
    stories = [
        i
        for i in (
            await client.get("/api/feed?min_score=20&since_hours=24", headers=headers)
        ).json()["items"]
        if "Reliance" in i["title"]
    ]
    assert len(stories) == 3

    first = await client.post(
        f"/api/stories/{stories[0]['story_id']}/feedback", headers=headers,
        json={"profile_id": profile_id, "verdict": "not_relevant"},
    )  # fmt: skip
    body = first.json()
    assert {"label": "Entity: Reliance Industries", "from": 5, "to": 4} in body["adjustments"]
    assert body["suggestion"] is None

    for story in stories[1:]:
        last = await client.post(
            f"/api/stories/{story['story_id']}/feedback", headers=headers,
            json={"profile_id": profile_id, "verdict": "not_relevant"},
        )  # fmt: skip
    suggestion = last.json()["suggestion"]
    assert suggestion["value"] == "reliance" and suggestion["count"] == 3

    profile = (await client.get(f"/api/profiles/{profile_id}", headers=headers)).json()
    reliance = next(r for r in profile["rules"] if r["value"] == "reliance")
    assert (reliance["weight"], reliance["base_weight"]) == (2, 5)  # three steps down

    reset = await client.post(f"/api/profiles/{profile_id}/reset-learning", headers=headers)
    assert reset.json()["reset"] >= 1
    profile = (await client.get(f"/api/profiles/{profile_id}", headers=headers)).json()
    assert next(r for r in profile["rules"] if r["value"] == "reliance")["weight"] == 5


async def test_learning_never_drifts_far_from_the_users_setting(api, db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "feedback_max_drift", 1)
    client = api()
    headers = await sign_up(client)
    profile_id = (await client.get("/api/profiles", headers=headers)).json()[0]["id"]
    et = await add_source(db, "et-markets", "https://economictimes.indiatimes.com", name="ET")
    await add_article(db, et, "Reliance Industries shares rise 2%", 10)
    await add_article(db, et, "Reliance Industries plans new factory", 11)
    await pipeline(db)
    items = (await client.get("/api/feed?min_score=20", headers=headers)).json()["items"]
    for item in items:
        await client.post(
            f"/api/stories/{item['story_id']}/feedback", headers=headers,
            json={"profile_id": profile_id, "verdict": "not_relevant"},
        )  # fmt: skip
    profile = (await client.get(f"/api/profiles/{profile_id}", headers=headers)).json()
    assert next(r for r in profile["rules"] if r["value"] == "reliance")["weight"] == 4


# --------------------------------------------------------------------------- admin


async def make_admin(db, email: str) -> None:
    async with db() as session, session.begin():
        await session.execute(update(User).where(User.email == email).values(is_admin=True))


async def test_admin_endpoints_require_admin(api) -> None:
    client = api()
    headers = await sign_up(client)
    for path in ["/api/admin/overview", "/api/admin/sources", "/api/admin/users"]:
        assert (await client.get(path, headers=headers)).status_code == 403


async def test_admin_sources(api, db) -> None:
    client = api()
    headers = await sign_up(client, "admin@example.com")
    await make_admin(db, "admin@example.com")
    await rbi_source(db)

    overview = (await client.get("/api/admin/overview", headers=headers)).json()
    assert overview["users"]["total"] == 1 and "telegram_configured" in overview

    created = await client.post(
        "/api/admin/sources", headers=headers,
        json={"name": "Hindu BusinessLine Markets", "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss",
              "category": "press_india", "authority": 3, "poll_interval_seconds": 300},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == "hindu-businessline-markets"
    duplicate = await client.post(
        "/api/admin/sources", headers=headers,
        json={"name": "Hindu BusinessLine Markets", "url": "https://other.example/rss", "category": "press_india"},
    )  # fmt: skip
    assert duplicate.status_code == 409

    patched = await client.patch(
        "/api/admin/sources/hindu-businessline-markets", headers=headers,
        json={"enabled": False, "poll_interval_seconds": 600},
    )  # fmt: skip
    assert (patched.json()["enabled"], patched.json()["poll_interval_seconds"]) == (False, 600)
    assert (
        await client.post("/api/admin/sources/hindu-businessline-markets/poll-now", headers=headers)
    ).status_code == 409

    sources = (await client.get("/api/admin/sources", headers=headers)).json()
    assert {s["slug"] for s in sources} == {"rbi-press-releases", "hindu-businessline-markets"}
    assert all("errors_24h" in s for s in sources)

    bad = await client.post(
        "/api/admin/sources",
        headers=headers,
        json={"name": "X", "url": "ftp://x", "category": "other"},
    )
    assert bad.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6379/",
        "http://localhost/feed",
        "http://10.1.2.3/rss",
        "http://169.254.169.254/latest",
    ],
)
async def test_admin_feed_test_refuses_internal_addresses(api, db, url: str) -> None:
    client = api()
    headers = await sign_up(client, "admin@example.com")
    await make_admin(db, "admin@example.com")
    result = (
        await client.post("/api/admin/sources/test", headers=headers, json={"url": url})
    ).json()
    assert result["ok"] is False
    assert "non-public" in result["error"] or "resolve" in result["error"]


async def test_admin_can_disable_a_user_immediately(api, db) -> None:
    admin, member = api(), api()
    admin_headers = await sign_up(admin, "admin@example.com")
    await make_admin(db, "admin@example.com")
    member_headers = await sign_up(member, "member@example.com")
    member_id = await user_id_of(member, member_headers)

    users = (await admin.get("/api/admin/users?q=member", headers=admin_headers)).json()
    assert users["total"] == 1 and users["items"][0]["profiles"] == 1

    resp = await admin.patch(
        f"/api/admin/users/{member_id}", headers=admin_headers, json={"is_active": False}
    )
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert (await member.get("/api/auth/me", headers=member_headers)).status_code == 401

    admin_id = await user_id_of(admin, admin_headers)
    self_demote = await admin.patch(
        f"/api/admin/users/{admin_id}", headers=admin_headers, json={"is_admin": False}
    )
    assert self_demote.status_code == 409


# --------------------------------------------------------------------------- SSRF guard


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.8",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
    ],
)
async def test_private_addresses_are_blocked(host: str) -> None:
    with pytest.raises(BlockedAddressError):
        await assert_public_host(host)


async def test_public_address_is_allowed() -> None:
    await assert_public_host("8.8.8.8")


async def test_notification_settings_validation(api) -> None:
    client = api()
    headers = await sign_up(client)
    half = await client.put("/api/notifications", headers=headers, json={"quiet_start": 60})
    assert half.status_code == 422
    bad_tz = await client.put("/api/notifications", headers=headers, json={"timezone": "Mars/Base"})
    assert bad_tz.status_code == 422
    ok = await client.put(
        "/api/notifications", headers=headers,
        json={"telegram_enabled": False, "quiet_start": 1320, "quiet_end": 420, "timezone": "Asia/Kolkata"},
    )  # fmt: skip
    assert ok.json()["quiet_start"] == 1320 and ok.json()["telegram_enabled"] is False
