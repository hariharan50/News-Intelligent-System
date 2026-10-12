import logging
import time
import uuid

from app.ai.service import summarise_pending
from app.alerts.telegram import deliver_pending_telegram
from app.cache.redis import HEARTBEAT_KEY, redis_client
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ingestion import http
from app.ingestion.service import claim_due_sources, poll_source
from app.processing.service import process_all_pending
from app.scoring.service import score_stories, score_unscored
from app.worker.broker import broker

log = logging.getLogger(__name__)
settings = get_settings()


@broker.task(task_name="system.heartbeat", schedule=[{"cron": "* * * * *"}])
async def heartbeat() -> None:
    """Proves scheduler -> Redis -> worker works. Read by /api/health/ready."""
    await redis_client.set(HEARTBEAT_KEY, str(time.time()), ex=3600)
    log.info("pipeline heartbeat recorded")


@broker.task(
    task_name="ingest.dispatch_due",
    schedule=[{"interval": settings.ingest_dispatch_interval_seconds}],
)
async def dispatch_due() -> int:
    """Claim sources whose next_poll_at has passed and fan out one poll task per source."""
    source_ids = await claim_due_sources(SessionLocal)
    for source_id in source_ids:
        await poll_source_task.kiq(str(source_id))
    if source_ids:
        log.info("dispatched %d source poll(s)", len(source_ids))
    return len(source_ids)


@broker.task(task_name="ingest.poll_source")
async def poll_source_task(source_id: str) -> None:
    result = await poll_source(
        uuid.UUID(source_id),
        session_factory=SessionLocal,
        client=http.get_client(),
        redis=redis_client,
    )
    if result.items_new:
        # Process right away so new items reach stories within seconds, not on the next tick.
        await process_pending_task.kiq()


@broker.task(task_name="process.pending", schedule=[{"interval": 30}])
async def process_pending_task() -> int:
    """Tag and cluster unprocessed articles, then score the touched stories for every
    profile that could care. Also scheduled as a safety net, in case a trigger was lost;
    the advisory lock makes overlapping runs harmless."""
    result = await process_all_pending(SessionLocal)
    if result.touched_story_ids:
        await score_stories(SessionLocal, list(result.touched_story_ids))
    return result.articles


@broker.task(task_name="alerts.telegram_sweep", schedule=[{"interval": 5}])
async def telegram_sweep_task() -> int:
    """Send pending Telegram alerts (a no-op when no bot is configured)."""
    return await deliver_pending_telegram(SessionLocal, redis=redis_client, http=http.get_client())


@broker.task(task_name="score.unscored", schedule=[{"interval": 120}])
async def score_unscored_task() -> int:
    """Safety net for stories that were updated but never (re)scored."""
    return await score_unscored(SessionLocal)


@broker.task(task_name="ai.summaries", schedule=[{"interval": 30}])
async def ai_summaries_task() -> int:
    """Write shared AI summaries for important new stories (no-op without an API key,
    when switched off, or over the daily budget)."""
    result = await summarise_pending(SessionLocal, redis=redis_client)
    return result.summarised


@broker.task(task_name="billing.sync", schedule=[{"interval": 600}])
async def billing_sync_task() -> int:
    """Re-fetch subscriptions from Razorpay in case a webhook was missed (no-op without keys)."""
    from app.billing.razorpay import RazorpayClient
    from app.billing.service import sync_subscriptions

    return await sync_subscriptions(SessionLocal, RazorpayClient(http=http.get_client()))


@broker.task(task_name="email.send", schedule=[{"interval": 5}])
async def email_send_task() -> int:
    """Deliver queued emails (verification, password reset, billing notices)."""
    from app.email.service import send_pending

    return await send_pending(SessionLocal, redis=redis_client)


@broker.task(task_name="email.trial_reminders", schedule=[{"cron": "15 * * * *"}])
async def trial_reminders_task() -> int:
    """Hourly: queue a "trial ends soon" email, once per trial."""
    from app.email.service import queue_trial_reminders

    return await queue_trial_reminders(SessionLocal)


@broker.task(
    task_name="agent.daily_briefs",
    schedule=[{"cron": "30 8 * * 1-5", "cron_offset": "Asia/Kolkata"}],
)
async def daily_briefs_task() -> int:
    """8:30 IST on weekdays: the pre-market brief for Pro+ users."""
    from app.agent.brief import run_daily_briefs

    return await run_daily_briefs(SessionLocal, redis_client)


@broker.task(
    task_name="maintenance.retention",
    schedule=[{"cron": "30 3 * * *", "cron_offset": "Asia/Kolkata"}],
)
async def retention_task() -> int:
    """03:30 IST: delete old news (unless saved), logs, sent emails and ended sessions."""
    from app.maintenance import run_retention

    return (await run_retention(SessionLocal)).total
