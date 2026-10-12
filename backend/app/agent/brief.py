"""The pre-market brief: at 8:30 IST on weekdays, a short summary of what the user's main
profile ranked highest since the previous afternoon. Pro+ only; one call per user."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape

import anthropic
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import service as agent_service
from app.agent.service import ClientFactory, number_citations
from app.agent.tools import StoryCatalog
from app.ai.claude import estimate_cost
from app.ai.service import AGENT_FEATURES, is_enabled, record_tokens, spent_today, today_ist
from app.billing.plans import entitlements_for
from app.core.config import get_settings
from app.models import DailyBrief, Profile, Story, StoryScore, TelegramLink, User

log = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=18)  # 8:30 IST back to the previous day's afternoon
MAX_STORIES = 12

BRIEF_PROMPT = """\
You write a short pre-market brief for an Indian trader, from the news stories listed in the
message. Use only those stories; never add facts, numbers or dates that aren't in them.

Format (plain text, no headings other than the two below):
Overnight
- 3 to 6 bullets, most important first, each one sentence ending with its story ref(s) in
  square brackets, e.g. [S2]. Merge stories about the same event into one bullet.
Watch today
- 1 to 3 bullets on scheduled events or open questions that the stories mention (a policy
  meeting, a results date, a deadline), each with its ref. Leave this part out if the
  stories mention none.

Keep it under 160 words. Plain, factual, no hype. Never recommend buying, selling or holding
anything, and don't predict prices."""


async def _stories_for(
    db: AsyncSession, profile: Profile, now: datetime
) -> list[tuple[Story, int]]:
    rows = await db.execute(
        select(Story, StoryScore.score)
        .join(StoryScore, StoryScore.story_id == Story.id)
        .where(
            StoryScore.profile_id == profile.id,
            StoryScore.story_published_at >= now - LOOKBACK,
            StoryScore.score >= profile.show_threshold,
        )
        .order_by(StoryScore.score.desc(), StoryScore.story_published_at.desc())
        .limit(MAX_STORIES)
    )
    return [tuple(r) for r in rows.all()]


def _prompt(items: list[dict], now_label: str) -> str:
    lines = [f"Brief for {now_label}. Stories, most important first:", ""]
    for item in items:
        lines.append(f"{item['ref']} | {item['published']} | {item['source']} | {item['title']}")
        detail = item.get("summary") or item.get("excerpt")
        if detail:
            lines.append(f"    {detail}")
    return "\n".join(lines)


def telegram_text(brief: DailyBrief) -> str:
    """The brief for Telegram (HTML): citation numbers become links."""
    links = {c["n"]: c["url"] for c in brief.citations}

    def link(match: re.Match[str]) -> str:
        n = int(match.group(1))
        return f' <a href="{escape(links[n])}">[{n}]</a>' if n in links else ""

    body = escape(brief.content)
    body = re.sub(r"\s*\[(\d+)\]", link, body)
    body = re.sub(r"^(Overnight|Watch today)$", r"<b>\1</b>", body, flags=re.MULTILINE)
    return f"☀️ <b>Pre-market brief · {brief.brief_date:%d %b}</b>\n\n{body}"


async def generate_brief(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    *,
    client_factory: ClientFactory | None = None,
    now: datetime | None = None,
) -> DailyBrief | None:
    """Write today's brief for one user, unless it exists or there's nothing to brief on."""
    s = get_settings()
    now = now or datetime.now(UTC)
    day = today_ist(now)
    async with session_factory() as db:
        if await db.scalar(
            select(DailyBrief.id).where(DailyBrief.user_id == user_id, DailyBrief.brief_date == day)
        ):
            return None
        profile = await db.scalar(
            select(Profile).where(Profile.user_id == user_id).order_by(Profile.created_at).limit(1)
        )
        if profile is None:
            return None
        rows = await _stories_for(db, profile, now)
        if not rows:
            return None
        catalog = StoryCatalog(db=db, user_id=user_id, profile_id=profile.id, now=now)
        items = await catalog._describe(rows)

        client = (client_factory or agent_service._default_client)()
        try:
            response = await client.beta.messages.create(
                model=s.ai_agent_model,
                max_tokens=6000,
                system=BRIEF_PROMPT,
                messages=[{"role": "user", "content": _prompt(items, f"{day:%A %d %B %Y}")}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("brief for %s failed: %s", user_id, exc)
            return None

        model = response.model or s.ai_agent_model
        usage = response.usage
        tokens_in = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        ok = response.stop_reason not in ("refusal", "max_tokens") and bool(text)
        await record_tokens(
            db,
            "brief",
            ok=ok,
            input_tokens=tokens_in,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost(model, tokens_in, usage.output_tokens),
        )
        if not ok:
            await db.commit()
            return None
        content, citations = number_citations(text, catalog)
        brief = DailyBrief(
            user_id=user_id,
            profile_id=profile.id,
            brief_date=day,
            content=content,
            citations=citations,
            story_count=len(rows),
            model=model,
            created_at=now,
        )
        db.add(brief)
        await db.commit()
        return brief


async def send_to_telegram(session_factory, redis: Redis, brief: DailyBrief) -> bool:
    from app.alerts.telegram import _wait_for_send_slot
    from app.ingestion import http
    from app.telegram.client import TelegramError, make_client

    if not get_settings().telegram_configured:
        return False
    async with session_factory() as db:
        link = await db.scalar(select(TelegramLink).where(TelegramLink.user_id == brief.user_id))
    if link is None:
        return False
    try:
        await _wait_for_send_slot(redis, link.chat_id)
        await make_client(http.get_client()).send_message(link.chat_id, telegram_text(brief))
    except TelegramError as exc:
        log.warning("brief to Telegram failed for %s: %s", brief.user_id, exc)
        return False
    return True


async def run_daily_briefs(
    session_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    *,
    client_factory: ClientFactory | None = None,
) -> int:
    """Briefs for every Pro+ user, within the agent's daily budget. Returns how many."""
    s = get_settings()
    if not s.ai_configured or not await is_enabled(redis):
        return 0
    async with session_factory() as db:
        user_ids = list(await db.scalars(select(User.id).where(User.is_active)))
        plans = await entitlements_for(db, user_ids)
    eligible = [uid for uid in user_ids if plans[uid].agent == "full"]
    budget = Decimal(str(s.ai_agent_daily_budget_usd))
    written = 0
    for user_id in eligible:
        async with session_factory() as db:
            if await spent_today(db, features=AGENT_FEATURES) >= budget:
                log.warning("daily briefs stopped at the AI agent budget (%d written)", written)
                break
        brief = await generate_brief(session_factory, user_id, client_factory=client_factory)
        if brief is None:
            continue
        written += 1
        if plans[user_id].telegram:
            await send_to_telegram(session_factory, redis, brief)
    return written


async def latest_brief(db: AsyncSession, user_id: uuid.UUID) -> DailyBrief | None:
    return await db.scalar(
        select(DailyBrief)
        .where(DailyBrief.user_id == user_id)
        .order_by(DailyBrief.brief_date.desc())
        .limit(1)
    )
