"""Ask the news and the daily brief (needs Docker PostgreSQL/Redis). Claude is replaced by a
scripted fake that streams the same event shapes as the SDK."""

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.agent import service as agent_service
from app.agent.brief import run_daily_briefs, telegram_text
from app.agent.service import number_citations
from app.agent.tools import SearchIn, StoryCatalog, TopIn
from app.ai.service import today_ist
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.models import AgentQuestion, AiUsageDaily, DailyBrief, User
from tests.test_alerts_telegram_admin import rbi_source
from tests.test_profiles_feed_api import add_article, add_source, pipeline, sign_up

pytestmark = pytest.mark.usefixtures("db")

RBI = "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance"
CRUDE = "Brent crude jumps 4% after supply disruption in the Red Sea"


# --------------------------------------------------------------------------- fake Claude


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, args: dict):
    return SimpleNamespace(
        type="tool_use", id=f"toolu_{uuid.uuid4().hex[:10]}", name=name, input=args
    )


def message(*blocks, stop_reason=None):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    return SimpleNamespace(
        model="claude-opus-5-5", content=list(blocks), stop_reason=stop_reason, usage=usage
    )


class FakeStream:
    def __init__(self, msg) -> None:
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self):
        async def events():
            for block in self.msg.content:
                if block.type == "text":
                    for word in block.text.split(" "):
                        yield SimpleNamespace(type="text", text=word + " ")

        return events()

    async def get_final_message(self):
        return self.msg


class FakeClaude:
    """Replays scripted turns. `requests` keeps every request's kwargs."""

    def __init__(self, turns: list) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []
        messages = SimpleNamespace(stream=self._stream, create=self._create)
        self.beta = SimpleNamespace(messages=messages)

    def _next(self, kwargs):
        # Copy: the agent keeps appending to the same list after the call.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.turns.pop(0)

    def _stream(self, **kwargs):
        return FakeStream(self._next(kwargs))

    async def _create(self, **kwargs):
        return self._next(kwargs)


@pytest.fixture
def claude(monkeypatch):
    """Configure AI and route every agent call to a FakeClaude set by the test."""
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    holder = SimpleNamespace(fake=None)
    monkeypatch.setattr(agent_service, "_default_client", lambda: holder.fake)
    return holder


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture
async def news(api, db):
    client = api()
    headers = await sign_up(client)
    rbi = await rbi_source(db)
    et = await add_source(
        db, "et-markets", "https://economictimes.indiatimes.com", name="ET Markets"
    )
    await add_article(db, rbi, RBI, 30)
    await add_article(db, et, CRUDE, 60)
    await pipeline(db)
    return SimpleNamespace(client=client, headers=headers)


async def ask(news, question: str, **extra) -> tuple[int, list[tuple[str, dict]] | dict]:
    resp = await news.client.post(
        "/api/agent/ask", json={"question": question, **extra}, headers=news.headers
    )
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        return resp.status_code, parse_sse(resp.text)
    return resp.status_code, resp.json()


# --------------------------------------------------------------------------- unit


def test_citations_are_numbered_in_order_and_unknown_refs_dropped() -> None:
    catalog = StoryCatalog(db=None, user_id=uuid.uuid4())
    for ref in ("S1", "S2"):
        catalog.by_ref[ref] = SimpleNamespace(as_citation=lambda n, ref=ref: {"n": n, "ref": ref})
    text, cites = number_citations("Crude rose [S2]. RBI held [S1, S2]. Rumour [S9].", catalog)
    assert text == "Crude rose [1]. RBI held [2][1]. Rumour ."
    assert cites == [{"n": 1, "ref": "S2"}, {"n": 2, "ref": "S1"}]


# --------------------------------------------------------------------------- tools


async def test_search_by_keyword_and_by_company_or_institution(db, news) -> None:
    async with db() as session:
        user_id = (await session.scalars(select(User.id))).one()
        catalog = await StoryCatalog.for_user(session, user_id)
        by_words = await catalog.search(SearchIn(query="crude oil supply"))
        assert by_words["results"][0]["title"] == CRUDE
        by_entity = await catalog.search(SearchIn(query="Reserve Bank of India"))
        assert by_entity["results"][0]["title"] == RBI
        assert by_entity["results"][0]["ref"] in catalog.by_ref
        assert "importance_for_user" in by_entity["results"][0]

        top = await catalog.top(TopIn(hours=24))
        assert top["results"][0]["title"] == RBI  # pinned at 95+ for traders
        # The same story keeps its ref across calls.
        assert top["results"][0]["ref"] == by_entity["results"][0]["ref"]

        out, is_error = await catalog.run("get_story", {"ref": by_entity["results"][0]["ref"]})
        assert not is_error and json.loads(out)["reports"][0]["source"] == "RBI Press Releases"
        out, is_error = await catalog.run("get_story", {"ref": "nope"})
        assert is_error
        out, is_error = await catalog.run("search_stories", {"query": "the news today"})
        assert not is_error and json.loads(out)["results"] == []


# --------------------------------------------------------------------------- ask


async def test_ask_streams_status_and_a_cited_answer(db, news, claude) -> None:
    claude.fake = FakeClaude([
        message(tool_block("search_stories", {"query": "repo rate"})),
        message(text_block("The RBI kept the repo rate at 5.5% [S1].")),
    ])  # fmt: skip
    status, events = await ask(news, "What did the RBI do?")
    assert status == 200
    kinds = [k for k, _ in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert events[0][1]["text"] == "Searching stories: repo rate"
    assert "".join(e["text"] for k, e in events if k == "delta").strip().startswith("The RBI kept")
    done = events[-1][1]
    assert done["answer"] == "The RBI kept the repo rate at 5.5% [1]."
    assert done["citations"][0]["title"] == RBI
    assert done["citations"][0]["url"].startswith("https://")

    first, second = claude.fake.requests
    assert {t["name"] for t in first["tools"]} == {"search_stories", "top_stories", "get_story"}
    assert all(t["eager_input_streaming"] for t in first["tools"])
    assert first["fallbacks"] == "default" and first["model"] == get_settings().ai_agent_model
    assert "Now:" in first["messages"][-1]["content"]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and RBI in tool_result["content"]

    async with db() as session:
        [row] = list(await session.scalars(select(AgentQuestion)))
        usage = await session.get(AiUsageDaily, (today_ist(), "agent"))
    assert row.status == "ok" and row.tool_calls == 1 and row.input_tokens == 2000
    assert usage.requests == 1

    state = (await news.client.get("/api/agent", headers=news.headers)).json()
    assert state["used_today"] == 1 and state["limit"] == get_settings().ai_agent_questions_limited
    assert state["recent"][0]["question"] == "What did the RBI do?"


async def test_follow_up_sends_the_earlier_exchange(db, news, claude) -> None:
    claude.fake = FakeClaude([
        message(tool_block("search_stories", {"query": "crude"})),
        message(text_block("Brent rose 4% [S1].")),
        message(text_block("It was a supply disruption in the Red Sea.")),
    ])  # fmt: skip
    _, events = await ask(news, "What happened to crude?")
    conversation = events[-1][1]["conversation_id"]
    await ask(news, "Why?", conversation_id=conversation)
    history = claude.fake.requests[-1]["messages"]
    assert history[0] == {"role": "user", "content": "What happened to crude?"}
    assert history[1]["content"].startswith("Brent rose 4%.")  # citation markers stripped
    assert CRUDE in history[1]["content"]
    assert history[2]["content"].endswith("Why?")


async def test_last_round_must_answer(db, news, claude, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_agent_max_tool_rounds", 1)
    claude.fake = FakeClaude([
        message(tool_block("top_stories", {"hours": 24})),
        message(text_block("Top story: RBI held rates [S1].")),
    ])  # fmt: skip
    _, events = await ask(news, "What happened today?")
    assert events[-1][0] == "done"
    assert claude.fake.requests[0]["tool_choice"] == {"type": "auto"}
    assert claude.fake.requests[1]["tool_choice"] == {"type": "none"}


async def test_refusal_is_reported(db, news, claude) -> None:
    claude.fake = FakeClaude([message(stop_reason="refusal")])
    _, events = await ask(news, "Something odd")
    assert events == [("error", {"code": "refused", "message": events[0][1]["message"]})]
    async with db() as session:
        [row] = list(await session.scalars(select(AgentQuestion)))
    assert row.status == "refused"


async def test_limits_plan_quota_and_configuration(api, db, news, claude, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_agent_questions_limited", 1)
    claude.fake = FakeClaude([message(text_block("Nothing found."))])
    assert (await ask(news, "First question"))[0] == 200
    status, body = await ask(news, "Second question")
    assert status == 429 and body["detail"]["code"] == "daily_limit"

    # Free plan: no agent.
    async with db() as session, session.begin():
        await session.execute(update(User).values(trial_ends_at=None))
    status, body = await ask(news, "Third question")
    assert status == 403 and body["detail"]["code"] == "plan_required"

    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    async with db() as session, session.begin():
        await session.execute(update(User).values(trial_ends_at=None))
    state = (await news.client.get("/api/agent", headers=news.headers)).json()
    assert state["available"] is False and state["plan_allows"] is False


async def test_question_validation(news, claude) -> None:
    status, body = await ask(news, "  ")
    assert status == 422


# --------------------------------------------------------------------------- daily brief


async def test_daily_brief_for_pro_plus_once_a_day(api, db, news, claude, monkeypatch) -> None:
    claude.fake = FakeClaude([])
    assert await run_daily_briefs(db, redis_client) == 0  # trial is Pro: no brief

    monkeypatch.setattr(get_settings(), "trial_plan", "pro_plus")
    claude.fake = FakeClaude([
        message(text_block("Overnight\n- RBI kept the repo rate at 5.5% [S1].\n- Brent rose 4% [S2].")),
    ])  # fmt: skip
    assert await run_daily_briefs(db, redis_client) == 1
    assert await run_daily_briefs(db, redis_client) == 0  # one per day
    request = claude.fake.requests[0]
    assert RBI in request["messages"][0]["content"] and "S1 |" in request["messages"][0]["content"]

    brief = (await news.client.get("/api/briefs/latest", headers=news.headers)).json()
    assert brief["content"].startswith("Overnight\n- RBI kept the repo rate at 5.5% [1].")
    assert [c["n"] for c in brief["citations"]] == [1, 2]

    async with db() as session:
        row = (await session.scalars(select(DailyBrief))).one()
    tg = telegram_text(row)
    assert "<b>Overnight</b>" in tg and '<a href="' in tg and "[1]</a>" in tg
