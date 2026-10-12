"""Data retention: delete what we no longer need, so the database stays small and we keep
personal data no longer than necessary. Runs nightly; safe to run any time.

Kept on purpose: stories someone saved (with their articles), users and their settings,
payments and billing events (accounting), the admin audit log.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models import (
    AgentQuestion,
    Article,
    EmailOutbox,
    EmailToken,
    SourceFetch,
    Story,
    StoryUserState,
    UserSession,
)

log = logging.getLogger(__name__)

BATCH = 2000  # rows per delete statement: short transactions, no long table locks


@dataclass
class RetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.deleted.values())


async def _delete_in_batches(db_factory, table, id_query) -> int:
    """Delete rows whose id is returned by `id_query`, BATCH at a time."""
    total = 0
    while True:
        async with db_factory() as db:
            ids = list(await db.scalars(id_query.limit(BATCH)))
            if not ids:
                return total
            await db.execute(delete(table).where(table.id.in_(ids)))
            await db.commit()
            total += len(ids)
            if len(ids) < BATCH:
                return total


async def run_retention(
    session_factory: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> RetentionReport:
    s = get_settings()
    now = now or datetime.now(UTC)
    report = RetentionReport()
    news_cutoff = now - timedelta(days=s.retention_news_days)

    saved = exists().where(
        StoryUserState.story_id == Story.id, StoryUserState.saved_at.is_not(None)
    )
    # Old stories nobody saved (scores, alerts, summaries and read state go with them).
    old_stories = select(Story.id).where(Story.last_published_at < news_cutoff, ~saved)
    # Articles of those stories first (the story FK would only null them out)...
    report.deleted["articles"] = await _delete_in_batches(
        session_factory,
        Article,
        select(Article.id).where(Article.story_id.in_(old_stories.limit(BATCH))),
    )
    report.deleted["stories"] = await _delete_in_batches(session_factory, Story, old_stories)
    # ...and old articles that never joined a story.
    report.deleted["articles"] += await _delete_in_batches(
        session_factory,
        Article,
        select(Article.id).where(Article.story_id.is_(None), Article.fetched_at < news_cutoff),
    )

    log_cutoff = now - timedelta(days=s.retention_log_days)
    report.deleted["source_fetches"] = await _delete_in_batches(
        session_factory,
        SourceFetch,
        select(SourceFetch.id).where(SourceFetch.started_at < log_cutoff),
    )
    # Sent or failed emails (their bodies contain names and, until used, link tokens).
    report.deleted["emails"] = await _delete_in_batches(
        session_factory,
        EmailOutbox,
        select(EmailOutbox.id).where(
            EmailOutbox.status != "pending", EmailOutbox.created_at < log_cutoff
        ),
    )
    report.deleted["email_tokens"] = await _delete_in_batches(
        session_factory,
        EmailToken,
        select(EmailToken.id).where(
            or_(
                EmailToken.used_at < now - timedelta(days=7),
                EmailToken.expires_at < now - timedelta(days=7),
            )
        ),
    )
    report.deleted["sessions"] = await _delete_in_batches(
        session_factory,
        UserSession,
        select(UserSession.id).where(
            or_(
                and_(UserSession.revoked_at.is_not(None), UserSession.revoked_at < log_cutoff),
                UserSession.expires_at < log_cutoff,
            )
        ),
    )
    report.deleted["agent_questions"] = await _delete_in_batches(
        session_factory,
        AgentQuestion,
        select(AgentQuestion.id).where(
            AgentQuestion.created_at < now - timedelta(days=s.retention_agent_days)
        ),
    )
    log.info("retention: deleted %s", {k: v for k, v in report.deleted.items() if v})
    return report
