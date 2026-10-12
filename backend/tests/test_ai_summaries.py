"""AI summaries: selection, budget, switch, storage, feed and admin (needs Docker PostgreSQL/Redis
for the integration tests). Claude itself is never called: a fake summarizer stands in."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.ai import service as ai
from app.ai.claude import (
    ClaudeSummarizer,
    ReportIn,
    StoryIn,
    SummaryOut,
    SummaryResult,
    build_prompt,
    estimate_cost,
)
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.models import AiUsageDaily, Profile, Story, StorySummary
from tests.test_alerts_telegram_admin import make_admin
from tests.test_profiles_feed_api import add_article, pipeline, sign_up

# --------------------------------------------------------------------------- unit


def test_cost_estimate_uses_model_prices_and_errs_high_for_unknown_models() -> None:
    assert estimate_cost("claude-opus-5-5", 1_000_000, 0) == Decimal(4)
    assert estimate_cost("claude-opus-5-5", 1000, 500) == Decimal("0.014")
    assert estimate_cost("some-future-model", 1_000_000, 0) == Decimal(10)


def test_prompt_contains_only_what_we_hold() -> None:
    story = StoryIn(
        title="RBI keeps repo rate at 5.5%",
        event_type="rate_decision",
        entities=["rbi"],
        reports=[
            ReportIn("RBI", "RBI keeps repo rate at 5.5%", "The MPC voted 6-0 ...", datetime(2026, 10, 1, 4, 30, tzinfo=UTC)),
            ReportIn("Mint", "RBI holds rates", None, datetime(2026, 10, 1, 4, 40, tzinfo=UTC)),
        ],
    )  # fmt: skip
    prompt = build_prompt(story)
    assert "Headline: RBI keeps repo rate at 5.5%" in prompt
    assert "Event type (our tag): rate decision" in prompt
    assert "Report 1 - RBI, 01 Oct 2026 04:30 UTC" in prompt
    assert "Excerpt: The MPC voted 6-0" in prompt
    assert prompt.count("Excerpt:") == 1  # no excerpt line when the feed gave none


def _fake_response(stop_reason="end_turn", parsed=None, model="claude-opus-5-5"):
    usage = SimpleNamespace(
        input_tokens=800, output_tokens=200, cache_read_input_tokens=0,
        cache_creation_input_tokens=None,
    )  # fmt: skip
    return SimpleNamespace(
        model=model, usage=usage, stop_reason=stop_reason, parsed_output=parsed,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
    )  # fmt: skip


def _fake_client(response):
    calls: list[dict] = []

    async def parse(**kwargs):
        calls.append(kwargs)
        return response

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    return client, calls


STORY = StoryIn("Headline", None, [], [])


async def test_claude_summarizer_maps_success_and_cost() -> None:
    out = SummaryOut(
        summary="RBI held rates.", key_facts=["Repo 5.5%"], affected=["Banks"], market_impact=4
    )
    client, calls = _fake_client(_fake_response(parsed=out))
    result = await ClaudeSummarizer(client=client).summarize(STORY)
    assert result.status == "ok" and result.output == out
    assert result.cost_usd == estimate_cost("claude-opus-5-5", 800, 200)
    request = calls[0]
    assert request["model"] == get_settings().ai_summary_model
    assert request["output_format"] is SummaryOut
    assert request["fallbacks"] == "default"
    assert "thinking" not in request  # always on for this model; effort controls depth


async def test_claude_summarizer_reports_refusals_and_truncation() -> None:
    client, _ = _fake_client(_fake_response(stop_reason="refusal"))
    refused = await ClaudeSummarizer(client=client).summarize(STORY)
    assert refused.status == "refused" and "cyber" in refused.error
    assert refused.cost_usd > 0  # declined requests can still be billed

    client, _ = _fake_client(_fake_response(stop_reason="max_tokens"))
    assert (await ClaudeSummarizer(client=client).summarize(STORY)).status == "failed"


# --------------------------------------------------------------------------- integration


class FakeSummarizer:
    def __init__(self, status: str = "ok", cost: str = "0.01", impact: int = 3) -> None:
        self.status = status
        self.impact = impact
        self.cost = Decimal(cost)
        self.seen: list[StoryIn] = []

    async def summarize(self, story: StoryIn) -> SummaryResult:
        self.seen.append(story)
        output = None
        if self.status == "ok":
            output = SummaryOut(
                summary=f"Summary of: {story.title}",
                key_facts=["Fact one", "  "],
                affected=["Indian banks"],
                market_impact=self.impact,
            )
        return SummaryResult(
            status=self.status, output=output, model="claude-opus-5-5", input_tokens=700,
            output_tokens=150, cost_usd=self.cost, error=None if output else "boom",
        )  # fmt: skip


@pytest.fixture
async def scored_news(api, db):
    """A signed-up trader, then news: one critical RBI story and some low ones."""
    client = api()
    headers = await sign_up(client)
    from tests.test_profiles_feed_api import add_source

    rbi = await add_source(
        db, "rbi-press-releases", "https://www.rbi.org.in", name="RBI Press Releases",
        category="official_india", licence_class="official", authority=5,
    )  # fmt: skip
    mint = await add_source(db, "mint-markets", "https://www.livemint.com", name="Mint - Markets")
    await add_article(
        db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 30
    )
    await add_article(db, mint, "IPL auction: record bid for Indian all-rounder", 5)
    await pipeline(db)
    return SimpleNamespace(client=client, headers=headers, rbi=rbi, mint=mint)


async def _summaries(db) -> list[StorySummary]:
    async with db() as session:
        return list(await session.scalars(select(StorySummary)))


async def test_summarises_only_important_stories_and_shows_them_in_the_feed(
    db, scored_news
) -> None:
    fake = FakeSummarizer()
    run = await ai.summarise_pending(db, redis=redis_client, summarizer=fake)
    assert run.summarised == 1 and run.failed == 0
    assert [s.title for s in fake.seen] == [
        "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance"
    ]

    [row] = await _summaries(db)
    assert row.status == "ok" and row.revisions == 1
    assert row.key_facts == ["Fact one"]  # blanks dropped

    feed = (await scored_news.client.get("/api/feed", headers=scored_news.headers)).json()
    by_title = {item["title"]: item for item in feed["items"]}
    rbi = by_title["RBI keeps repo rate unchanged at 5.5%, maintains neutral stance"]
    assert rbi["ai"]["summary"].startswith("Summary of:")
    assert rbi["ai"]["affected"] == ["Indian banks"] and rbi["ai"]["outdated"] is False

    # Nothing left to do on the next run.
    assert (await ai.summarise_pending(db, redis=redis_client, summarizer=fake)).summarised == 0

    async with db() as session:
        usage = await session.get(AiUsageDaily, (ai.today_ist(), "summary"))
    assert usage.requests == 1 and usage.input_tokens == 700 and usage.cost_usd == Decimal("0.01")


async def test_rewrites_when_more_reports_arrive(db, scored_news) -> None:
    fake = FakeSummarizer()
    await ai.summarise_pending(db, redis=redis_client, summarizer=fake)
    await add_article(
        db, scored_news.mint, "RBI holds repo rate at 5.5% as expected; rupee steady", 20
    )
    await pipeline(db)

    feed = (await scored_news.client.get("/api/feed", headers=scored_news.headers)).json()
    rbi = next(i for i in feed["items"] if i["title"].startswith("RBI keeps"))
    assert rbi["article_count"] == 2 and rbi["ai"]["outdated"] is True

    # Not rewritten within 10 minutes of the last summary...
    assert (await ai.summarise_pending(db, redis=redis_client, summarizer=fake)).summarised == 0
    async with db() as session, session.begin():
        await session.execute(
            update(StorySummary).values(updated_at=datetime.now(UTC) - timedelta(minutes=11))
        )
    # ...but after that, with both reports in the prompt.
    assert (await ai.summarise_pending(db, redis=redis_client, summarizer=fake)).summarised == 1
    assert len(fake.seen[-1].reports) == 2
    [row] = await _summaries(db)
    assert row.revisions == 2 and row.article_count == 2


async def test_daily_budget_stops_ai_work(db, scored_news, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_daily_budget_usd", 0.5)
    async with db() as session, session.begin():
        session.add(
            AiUsageDaily(
                day=ai.today_ist(), feature="summary", requests=50, cost_usd=Decimal("0.5")
            )
        )
    fake = FakeSummarizer()
    run = await ai.summarise_pending(db, redis=redis_client, summarizer=fake)
    assert run.skipped_reason == "over_budget" and fake.seen == []


async def test_switch_and_missing_key(db, scored_news, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    assert (await ai.summarise_pending(db, redis=redis_client)).skipped_reason == "not_configured"

    await ai.set_enabled(redis_client, False)
    fake = FakeSummarizer()
    run = await ai.summarise_pending(db, redis=redis_client, summarizer=fake)
    assert run.skipped_reason == "switched_off" and fake.seen == []


async def test_failures_retry_a_few_times_and_refusals_never(db, scored_news, monkeypatch) -> None:
    failing = FakeSummarizer(status="failed", cost="0")
    await ai.summarise_pending(db, redis=redis_client, summarizer=failing)
    [row] = await _summaries(db)
    assert row.status == "failed" and row.attempts == 1 and row.last_error == "boom"

    # Retried after a pause, up to ai_summary_max_attempts in total.
    for _ in range(5):
        async with db() as session, session.begin():
            await session.execute(
                update(StorySummary).values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
            )
        await ai.summarise_pending(db, redis=redis_client, summarizer=failing)
    [row] = await _summaries(db)
    assert row.attempts == get_settings().ai_summary_max_attempts
    assert len(failing.seen) == get_settings().ai_summary_max_attempts

    async with db() as session, session.begin():
        await session.execute(update(StorySummary).values(status="refused", attempts=1))
    refusing = FakeSummarizer(status="refused")
    await ai.summarise_pending(db, redis=redis_client, summarizer=refusing)
    assert refusing.seen == []

    # A story without a usable summary simply shows none.
    feed = (await scored_news.client.get("/api/feed", headers=scored_news.headers)).json()
    assert all(item["ai"] is None for item in feed["items"])


async def test_deleted_story_cascades(db, scored_news) -> None:
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer())
    async with db() as session, session.begin():
        await session.execute(Story.__table__.delete())
    assert await _summaries(db) == []


async def test_admin_ai_status_and_switch(api, db, scored_news) -> None:
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer())
    client = api()
    headers = await sign_up(client, "admin@example.com")
    assert (await client.get("/api/admin/ai", headers=headers)).status_code == 403
    await make_admin(db, "admin@example.com")

    status = (await client.get("/api/admin/ai", headers=headers)).json()
    assert status["enabled"] is True
    assert status["spent_today_usd"] == pytest.approx(0.01)
    assert status["summaries_24h"] == {"ok": 1}
    assert status["days"][0]["requests"] == 1

    off = (await client.put("/api/admin/ai", json={"enabled": False}, headers=headers)).json()
    assert off["enabled"] is False
    assert await ai.is_enabled(redis_client) is False
    overview = (await client.get("/api/admin/overview", headers=headers)).json()
    assert overview["ai"]["enabled"] is False


# --------------------------------------------------------------------------- importance check


CRUDE = "Brent crude jumps 4% after supply disruption in the Red Sea"


@pytest.fixture
async def crude_news(db, scored_news):
    """Adds an unpinned High-tier story (the RBI decision is pinned at 95+ for traders)."""
    from tests.test_profiles_feed_api import add_source

    et = await add_source(
        db, "et-markets", "https://economictimes.indiatimes.com", name="ET Markets"
    )
    await add_article(db, et, CRUDE, 40)
    await pipeline(db)
    return scored_news


async def _item(news, title: str) -> dict:
    feed = (await news.client.get("/api/feed", headers=news.headers)).json()
    return next(i for i in feed["items"] if i["title"] == title)


@pytest.mark.parametrize(("impact", "delta"), [(1, -15), (2, -8), (3, 0), (4, 8)])
async def test_market_impact_moves_scores_within_bounds(db, crude_news, impact, delta) -> None:
    before = await _item(crude_news, CRUDE)
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer(impact=impact))
    after = await _item(crude_news, CRUDE)

    assert after["score"] == max(0, min(100, before["score"] + delta))
    ai_reasons = [r for r in after["reasons"] if r["kind"] == "ai"]
    assert len(ai_reasons) == (0 if delta == 0 else 1)
    # The alert flag follows the rule score only.
    assert after["alert"] == before["alert"]
    async with db() as session:
        assert (await session.get(Story, after["story_id"])).ai_impact == impact


async def test_importance_check_can_be_switched_off(db, crude_news, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_importance_enabled", False)
    before = await _item(crude_news, CRUDE)
    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer(impact=1))
    after = await _item(crude_news, CRUDE)
    assert after["score"] == before["score"] and after["ai"] is not None


async def test_ai_adjustment_never_creates_an_alert(db, crude_news) -> None:
    from app.models import Alert

    async with db() as session:
        alerts_before = len(list(await session.scalars(select(Alert))))
    # Put the trader's alert line just above the crude story's rule score.
    crude = await _item(crude_news, CRUDE)
    async with db() as session, session.begin():
        await session.execute(update(Profile).values(alert_threshold=min(100, crude["score"] + 5)))

    await ai.summarise_pending(db, redis=redis_client, summarizer=FakeSummarizer(impact=5))
    after = await _item(crude_news, CRUDE)
    assert after["score"] >= crude["score"] + 5  # AI lifted it over the alert line...
    assert after["alert"] is False  # ...but it is not alert-worthy
    async with db() as session:
        assert len(list(await session.scalars(select(Alert)))) == alerts_before
