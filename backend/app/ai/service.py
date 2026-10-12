"""Shared AI story summaries: pick what to summarise, keep inside the daily budget, store.

Rules decide, AI assists: nothing in scoring, the feed or alerts waits for or depends on
this. If Claude is off, down or over budget, stories simply have no summary until it's back.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.claude import ReportIn, StoryIn, Summarizer, SummaryResult
from app.core.config import get_settings
from app.models import AiUsageDaily, Article, Source, Story, StoryScore, StorySummary
from app.models.ai import SummaryStatus

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
FEATURE_SUMMARY = "summary"
SWITCH_KEY = "nis:ai:enabled"  # "0" = switched off by an admin; anything else = on
RUN_LOCK = "nis:lock:ai-summaries"
CONCURRENCY = 4
# A summary is rewritten for new reports at most this often.
RESUMMARY_MIN_GAP = timedelta(minutes=10)


def today_ist(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(IST).date()


# --------------------------------------------------------------------------- switch & budget


async def is_enabled(redis: Redis) -> bool:
    return await redis.get(SWITCH_KEY) != "0"


async def set_enabled(redis: Redis, enabled: bool) -> None:
    await redis.set(SWITCH_KEY, "1" if enabled else "0")


# Each budget covers its own features: summaries can't eat the agent's budget or vice versa.
SUMMARY_FEATURES = ("summary",)
AGENT_FEATURES = ("agent", "brief")


async def spent_today(
    db: AsyncSession, day: date | None = None, features: tuple[str, ...] | None = None
) -> Decimal:
    query = select(func.coalesce(func.sum(AiUsageDaily.cost_usd), 0)).where(
        AiUsageDaily.day == (day or today_ist())
    )
    if features:
        query = query.where(AiUsageDaily.feature.in_(features))
    return Decimal(await db.scalar(query))


async def record_tokens(
    db: AsyncSession, feature: str, *, ok: bool, input_tokens: int, output_tokens: int,
    cost_usd: Decimal,
) -> None:  # fmt: skip
    values = {
        "day": today_ist(),
        "feature": feature,
        "requests": 1,
        "failures": 0 if ok else 1,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
    }
    stmt = pg_insert(AiUsageDaily).values(**values)
    t = AiUsageDaily.__table__.c
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[t.day, t.feature],
            set_={k: t[k] + stmt.excluded[k] for k in values if k not in ("day", "feature")},
        )
    )


async def record_usage(db: AsyncSession, feature: str, result: SummaryResult) -> None:
    await record_tokens(
        db, feature, ok=result.status == SummaryStatus.OK, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, cost_usd=result.cost_usd,
    )  # fmt: skip


# --------------------------------------------------------------------------- selection


async def stories_needing_summary(db: AsyncSession, now: datetime | None = None) -> list[Story]:
    """Recent stories that matter to someone and have no (current) summary, best first.

    - "matter": at least one profile scores the story at ai_summary_min_score or more;
    - new stories first get a summary; one whose summary predates newer reports gets
      rewritten, at most ai_summary_max_revisions times and not more often than every 10 min;
    - failed attempts are retried up to ai_summary_max_attempts; refusals are not retried.
    """
    s = get_settings()
    now = now or datetime.now(UTC)
    best = (
        select(func.max(StoryScore.score))
        .where(StoryScore.story_id == Story.id)
        .correlate(Story)
        .scalar_subquery()
    )
    relevant = exists().where(
        StoryScore.story_id == Story.id, StoryScore.score >= s.ai_summary_min_score
    )
    due = or_(
        StorySummary.story_id.is_(None),
        and_(
            StorySummary.status == SummaryStatus.FAILED,
            StorySummary.attempts < s.ai_summary_max_attempts,
            StorySummary.updated_at < now - timedelta(minutes=2),
        ),
        and_(
            StorySummary.status == SummaryStatus.OK,
            StorySummary.article_count < Story.article_count,
            StorySummary.revisions < s.ai_summary_max_revisions,
            StorySummary.updated_at < now - RESUMMARY_MIN_GAP,
        ),
    )
    rows = await db.scalars(
        select(Story)
        .outerjoin(StorySummary, StorySummary.story_id == Story.id)
        .where(
            Story.last_published_at >= now - timedelta(hours=s.ai_summary_max_age_hours),
            relevant,
            due,
        )
        .order_by(best.desc(), Story.last_published_at.desc())
        .limit(s.ai_summary_batch)
    )
    return list(rows)


async def story_input(db: AsyncSession, story: Story) -> StoryIn:
    """Headlines and feed excerpts we already hold: the only text a summary may use."""
    rows = await db.execute(
        select(Article.title, Article.excerpt, Article.published_at, Source.name)
        .join(Source, Source.id == Article.source_id)
        .where(Article.story_id == story.id)
        .order_by(Source.authority.desc(), Article.published_at)
        .limit(6)
    )
    return StoryIn(
        title=story.title,
        event_type=story.event_type,
        entities=list(story.entities),
        reports=[
            ReportIn(source=name, title=title, excerpt=excerpt, published_at=published)
            for title, excerpt, published, name in rows
        ],
    )


# --------------------------------------------------------------------------- run


async def _store(
    db: AsyncSession, story: Story, result: SummaryResult, previous: StorySummary | None
) -> None:
    now = datetime.now(UTC)
    ok = result.status == SummaryStatus.OK and result.output is not None
    if previous is None:
        previous = StorySummary(story_id=story.id, revisions=0, attempts=0, created_at=now)
        db.add(previous)
    if ok:
        out = result.output
        previous.status = SummaryStatus.OK
        previous.summary = out.summary.strip()
        previous.key_facts = [f.strip() for f in out.key_facts if f.strip()][:4]
        previous.affected = [a.strip() for a in out.affected if a.strip()][:4]
        story.ai_impact = out.market_impact
        previous.article_count = story.article_count
        previous.revisions = (previous.revisions or 0) + 1
        previous.attempts = 0
        previous.last_error = None
    elif previous.status == SummaryStatus.OK:
        # A rewrite failed: keep the old summary, but stop trying for these reports.
        previous.article_count = story.article_count
        previous.last_error = result.error
    else:
        previous.status = result.status
        previous.article_count = story.article_count
        previous.attempts = (previous.attempts or 0) + 1
        previous.last_error = result.error
    previous.model = result.model or previous.model
    previous.updated_at = now


@dataclass
class RunResult:
    summarised: int = 0
    failed: int = 0
    skipped_reason: str | None = None  # "not_configured" | "switched_off" | "over_budget" | "busy"


async def summarise_pending(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: Redis,
    summarizer: Summarizer | None = None,
) -> RunResult:
    """One run: summarise up to ai_summary_batch stories, stopping at the daily budget."""
    s = get_settings()
    if summarizer is None:
        if not s.ai_configured:
            return RunResult(skipped_reason="not_configured")
        from app.ai.claude import ClaudeSummarizer

        summarizer = ClaudeSummarizer()
    if not await is_enabled(redis):
        return RunResult(skipped_reason="switched_off")
    if not await redis.set(RUN_LOCK, "1", nx=True, ex=300):
        return RunResult(skipped_reason="busy")
    try:
        budget = Decimal(str(s.ai_daily_budget_usd))
        async with session_factory() as db:
            if await spent_today(db, features=SUMMARY_FEATURES) >= budget:
                return RunResult(skipped_reason="over_budget")
            stories = await stories_needing_summary(db)
            inputs = {story.id: await story_input(db, story) for story in stories}

        run = RunResult()
        rescore: list[uuid.UUID] = []
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def one(story: Story) -> None:
            async with semaphore:
                # Re-check between calls: concurrent calls can overshoot by at most a few.
                async with session_factory() as db:
                    if await spent_today(db, features=SUMMARY_FEATURES) >= budget:
                        run.skipped_reason = "over_budget"
                        return
                result = await summarizer.summarize(inputs[story.id])
                async with session_factory() as db:
                    current = await db.get(Story, story.id)
                    if current is None:  # deleted meanwhile
                        return
                    previous = await db.get(StorySummary, story.id)
                    impact_before = current.ai_impact
                    await _store(db, current, result, previous)
                    if current.ai_impact != impact_before:
                        rescore.append(story.id)
                    await record_usage(db, FEATURE_SUMMARY, result)
                    await db.commit()
                if result.status == SummaryStatus.OK:
                    run.summarised += 1
                else:
                    run.failed += 1

        await asyncio.gather(*(one(story) for story in stories))
        if rescore and s.ai_importance_enabled:
            # Apply the AI importance check to every profile's score. Alerts are unaffected:
            # they compare rule scores, which this doesn't change.
            from app.scoring.service import score_stories

            await score_stories(session_factory, rescore)
        if run.summarised or run.failed:
            log.info("AI summaries: %d written, %d failed", run.summarised, run.failed)
        return run
    finally:
        await redis.delete(RUN_LOCK)


async def summaries_for(
    db: AsyncSession, story_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StorySummary]:
    if not story_ids:
        return {}
    rows = await db.scalars(
        select(StorySummary).where(
            StorySummary.story_id.in_(story_ids), StorySummary.status == SummaryStatus.OK
        )
    )
    return {row.story_id: row for row in rows}
