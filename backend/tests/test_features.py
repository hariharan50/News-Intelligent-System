"""Personal mode: payments and AI switched off (FEATURE_BILLING / FEATURE_AI = false)."""

import pytest

from app.ai import service as ai
from app.cache.redis import redis_client
from app.core.config import get_settings
from tests.test_ai_summaries import FakeSummarizer
from tests.test_alerts_telegram_admin import rbi_source
from tests.test_profiles_feed_api import add_article, pipeline, sign_up


@pytest.fixture
def personal(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "feature_billing", False)
    monkeypatch.setattr(s, "feature_ai", False)
    monkeypatch.setattr(s, "anthropic_api_key", "sk-test")  # even with a key, AI stays off
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test")
    monkeypatch.setattr(s, "razorpay_key_secret", "x")


async def test_everything_is_unlocked_without_plans(api, db, personal) -> None:
    client = api()
    headers = await sign_up(client)
    me = (await client.get("/api/billing", headers=headers)).json()
    assert me["enabled"] is False and me["configured"] is False
    assert me["current"]["source"] == "unlimited"
    assert me["current"]["features"]["feed_delay_minutes"] == 0
    assert me["current"]["features"]["telegram"] is True
    assert me["current"]["features"]["max_saved"] is None

    assert (await client.get("/api/billing/plans")).json() == []
    resp = await client.post("/api/billing/checkout", json={"plan": "pro"}, headers=headers)
    assert resp.status_code == 404 and resp.json()["detail"]["code"] == "billing_disabled"
    assert (await client.get("/api/meta/features")).json() == {"billing": False, "ai": False, "google": False}

    # More than the free plan's single profile.
    for name in ("Second", "Third"):
        resp = await client.post(
            "/api/profiles",
            json={"name": name, "show_threshold": 50, "alert_threshold": 80, "rules": []},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text


async def test_ai_does_nothing_when_off(api, db, personal) -> None:
    client = api()
    headers = await sign_up(client)
    rbi = await rbi_source(db)
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 30
    )
    await pipeline(db)

    assert (await ai.summarise_pending(db, redis=redis_client)).skipped_reason == "not_configured"
    state = (await client.get("/api/agent", headers=headers)).json()
    assert state["enabled"] is False and state["available"] is False
    resp = await client.post(
        "/api/agent/ask", json={"question": "What did the RBI do?"}, headers=headers
    )
    assert resp.status_code == 503

    # Summaries written earlier (when AI was on) aren't shown, and don't move scores.
    get_settings().feature_ai = True
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer(impact=1))
    get_settings().feature_ai = False
    [item] = (await client.get("/api/feed", headers=headers)).json()["items"]
    assert item["ai"] is None and item["ai_locked"] is False
    assert all(r["kind"] != "ai" for r in item["reasons"])
