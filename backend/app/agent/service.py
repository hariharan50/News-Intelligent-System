"""Ask the news: a Claude tool-use loop over our stories, streamed as it works.

Events yielded to the API (and on to the browser):
  {"type": "status", "text": "Searching stories: crude oil"}   while tools run
  {"type": "reset"}                 discard text streamed so far (it preceded a tool call)
  {"type": "delta", "text": "..."}  answer text as it is written
  {"type": "done", ...}             final answer with numbered citations, saved
  {"type": "error", "code", "message"}
"""

import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from decimal import Decimal

import anthropic
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools import TOOLS, StoryCatalog
from app.ai.claude import estimate_cost
from app.ai.service import AGENT_FEATURES, IST, is_enabled, record_tokens, spent_today, today_ist
from app.billing.plans import entitlements
from app.core.config import get_settings
from app.models import AgentQuestion

log = logging.getLogger(__name__)

HISTORY_TURNS = 4  # earlier Q&A pairs of the same conversation sent as context
LOCK = "nis:lock:agent:{user_id}"

SYSTEM_PROMPT = """\
You answer questions about market-moving news for an Indian trader, using only the news
stories this app has collected. Use the tools to find stories; never answer from memory, and
never invent facts, figures or dates that aren't in the tool results.

How to work:
- Search with short keyword queries. Try a second query with different words if the first
  finds little. Use top_stories for broad "what happened" questions. Open a story with
  get_story only when you need its details.
- Two or three tool calls are usually enough. Stop searching once you can answer.

How to answer:
- Lead with the direct answer in one or two sentences, then the supporting points.
- Cite every factual statement with the story ref in square brackets, e.g. [S2], right after
  the sentence it supports. Only cite refs you received from a tool.
- If the stories don't answer the question, say so plainly and mention what you did find.
  Don't fill gaps with general knowledge.
- Say when something happened (dates and times are given in IST).
- Keep it short: usually under 150 words. Use "- " bullets for lists and **bold** sparingly.
  No headings, no tables.
- This is news, not advice: never recommend buying, selling or holding anything, and don't
  predict prices. If asked for advice, explain you can only report what the news says."""


class AgentError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- limits


def daily_limit(agent_level: str) -> int:
    s = get_settings()
    return {"limited": s.ai_agent_questions_limited, "full": s.ai_agent_questions_full}.get(
        agent_level, 0
    )


async def questions_today(db: AsyncSession, user_id: uuid.UUID) -> int:
    start = datetime.combine(today_ist(), datetime.min.time(), IST)
    return await db.scalar(
        select(func.count()).where(
            AgentQuestion.user_id == user_id,
            AgentQuestion.created_at >= start,
            AgentQuestion.status != "failed",  # our failures don't use up the allowance
        )
    )


async def status_for(db: AsyncSession, redis: Redis, user_id: uuid.UUID) -> dict:
    s = get_settings()
    plan = await entitlements(db, user_id)
    limit = daily_limit(plan.agent)
    used = await questions_today(db, user_id)
    available = s.ai_configured and await is_enabled(redis)
    over_budget = await spent_today(db, features=AGENT_FEATURES) >= Decimal(
        str(s.ai_agent_daily_budget_usd)
    )
    return {
        "enabled": s.feature_ai,
        "available": available and not over_budget,
        "reason": None
        if available and not over_budget
        else ("busy" if over_budget else "not_configured"),
        "plan_allows": limit > 0,
        "plan": plan.plan,
        "limit": limit,
        "used_today": used,
        "remaining": max(0, limit - used),
    }


async def check_allowed(db: AsyncSession, redis: Redis, user_id: uuid.UUID) -> None:
    st = await status_for(db, redis, user_id)
    if not st["plan_allows"]:
        raise AgentError(403, "plan_required", "Ask the news is part of Pro.")
    if not st["available"]:
        if st["reason"] == "busy":
            raise AgentError(
                503, "ai_busy", "The AI has reached today's limit. Try again tomorrow."
            )
        raise AgentError(503, "ai_unavailable", "The AI isn't available right now.")
    if st["remaining"] <= 0:
        raise AgentError(
            429,
            "daily_limit",
            f"You've used today's {st['limit']} questions. They reset at midnight IST."
            + (" Pro+ gets more." if st["plan"] == "pro" else ""),
        )


# --------------------------------------------------------------------------- citations

_CITE = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")


def number_citations(answer: str, catalog: StoryCatalog) -> tuple[str, list[dict]]:
    """[S7] -> [1] in order of first use; unknown refs are dropped. Returns text, citations."""
    order: dict[str, int] = {}

    def replace(match: re.Match[str]) -> str:
        out = []
        for ref in re.split(r"\s*[,;]\s*", match.group(1)):
            if ref in catalog.by_ref:
                n = order.setdefault(ref, len(order) + 1)
                out.append(f"[{n}]")
        return "".join(out)

    text = _CITE.sub(replace, answer)
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    citations = [catalog.by_ref[ref].as_citation(n) for ref, n in order.items()]
    return text, citations


def _strip_citations(text: str) -> str:
    return re.sub(r"\s*\[\d+\]", "", text)


# --------------------------------------------------------------------------- the loop


def _describe_call(name: str, args: object) -> str:
    args = args if isinstance(args, dict) else {}
    if name == "search_stories":
        return f"Searching stories: {str(args.get('query', ''))[:60]}"
    if name == "top_stories":
        return f"Reading your top stories of the last {args.get('hours', 24)} hours"
    if name == "get_story":
        return f"Opening story {args.get('ref', '')}"
    return "Working"


ClientFactory = Callable[[], anthropic.AsyncAnthropic]


def _default_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key, max_retries=2)


async def _history(db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> list[dict]:
    rows = list(
        await db.scalars(
            select(AgentQuestion)
            .where(
                AgentQuestion.user_id == user_id,
                AgentQuestion.conversation_id == conversation_id,
                AgentQuestion.status == "ok",
            )
            .order_by(AgentQuestion.created_at.desc())
            .limit(HISTORY_TURNS)
        )
    )
    messages: list[dict] = []
    for row in reversed(rows):
        cited = "; ".join(c["title"] for c in row.citations[:6])
        answer = _strip_citations(row.answer or "")
        if cited:
            answer += f"\n\n(Stories used: {cited})"
        messages += [
            {"role": "user", "content": row.question},
            {"role": "assistant", "content": answer},
        ]
    return messages


async def ask(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    user_id: uuid.UUID,
    question: str,
    conversation_id: uuid.UUID | None = None,
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[dict]:
    """Answer one question. Call check_allowed() first: this assumes the user may ask."""
    s = get_settings()
    conversation_id = conversation_id or uuid.uuid4()
    lock = LOCK.format(user_id=user_id)
    if not await redis.set(lock, "1", nx=True, ex=180):
        yield {
            "type": "error",
            "code": "busy",
            "message": "Wait for your previous question to finish.",
        }
        return

    input_tokens = output_tokens = tool_calls = 0
    cost = Decimal(0)
    model = s.ai_agent_model
    status, answer, citations = "failed", None, []
    try:
        async with session_factory() as db:
            catalog = await StoryCatalog.for_user(db, user_id)
            now_ist = datetime.now(UTC).astimezone(IST).strftime("%A %d %B %Y, %H:%M IST")
            messages = await _history(db, user_id, conversation_id)
            messages.append({"role": "user", "content": f"(Now: {now_ist})\n\n{question}"})
            client = (client_factory or _default_client)()

            final_text = ""
            for round_no in range(s.ai_agent_max_tool_rounds + 1):
                last_round = round_no == s.ai_agent_max_tool_rounds
                streamed = False
                async with client.beta.messages.stream(
                    model=s.ai_agent_model,
                    max_tokens=8000,
                    system=SYSTEM_PROMPT,
                    tools=[{**t, "eager_input_streaming": True} for t in TOOLS],
                    # The last round may not search again: it must answer with what it found.
                    tool_choice={"type": "none"} if last_round else {"type": "auto"},
                    messages=messages,
                    output_config={"effort": s.ai_agent_effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    async for event in stream:
                        if event.type == "text" and event.text:
                            streamed = True
                            yield {"type": "delta", "text": event.text}
                    response = await stream.get_final_message()

                model = response.model or model
                usage = response.usage
                round_in = (
                    usage.input_tokens
                    + (usage.cache_read_input_tokens or 0)
                    + (usage.cache_creation_input_tokens or 0)
                )
                input_tokens += round_in
                output_tokens += usage.output_tokens
                cost += estimate_cost(model, round_in, usage.output_tokens)

                if response.stop_reason == "refusal":
                    status = "refused"
                    yield {
                        "type": "error",
                        "code": "refused",
                        "message": "The AI declined this question. Try rephrasing it.",
                    }
                    break
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    final_text = "".join(b.text for b in response.content if b.type == "text")
                    break
                if response.stop_reason == "max_tokens":
                    yield {
                        "type": "error",
                        "code": "too_long",
                        "message": "The answer got too long. Try a narrower question.",
                    }
                    break
                if streamed:
                    yield {"type": "reset"}

                results = []
                for block in tool_uses:
                    tool_calls += 1
                    yield {"type": "status", "text": _describe_call(block.name, block.input)}
                    content, is_error = await catalog.run(block.name, block.input)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": results})

            if final_text:
                answer, citations = number_citations(final_text, catalog)
                status = "ok"
    except ValueError:
        log.warning("agent: unparseable tool input", exc_info=True)
        yield {"type": "error", "code": "ai_error", "message": "The AI stumbled. Please ask again."}
    except anthropic.APIStatusError as exc:
        log.warning("agent: Claude returned %s: %s", exc.status_code, exc.message)
        yield {
            "type": "error",
            "code": "ai_error",
            "message": "The AI is having trouble right now. Try again in a minute.",
        }
    except anthropic.APIConnectionError:
        yield {
            "type": "error",
            "code": "ai_error",
            "message": "Couldn't reach the AI. Try again in a minute.",
        }
    finally:
        await redis.delete(lock)

    async with session_factory() as db:
        row = AgentQuestion(
            created_at=datetime.now(UTC),
            user_id=user_id,
            conversation_id=conversation_id,
            question=question,
            answer=answer,
            citations=citations,
            status=status,
            tool_calls=tool_calls,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        db.add(row)
        if input_tokens or output_tokens:
            await record_tokens(
                db,
                "agent",
                ok=status == "ok",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
        await db.commit()
        if status == "ok":
            yield {
                "type": "done",
                "id": str(row.id),
                "conversation_id": str(conversation_id),
                "answer": answer,
                "citations": citations,
                "created_at": row.created_at.isoformat(),
            }


async def recent_questions(db: AsyncSession, user_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = await db.scalars(
        select(AgentQuestion)
        .where(AgentQuestion.user_id == user_id, AgentQuestion.status == "ok")
        .order_by(AgentQuestion.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(r.id),
            "conversation_id": str(r.conversation_id),
            "question": r.question,
            "answer": r.answer,
            "citations": r.citations,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
