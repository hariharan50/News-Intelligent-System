"""Data retention and monitoring privacy (needs Docker PostgreSQL/Redis for retention)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.monitoring import _scrub
from app.maintenance import run_retention
from app.models import Article, EmailOutbox, EmailToken, Story, UserSession
from tests.test_alerts_telegram_admin import rbi_source
from tests.test_profiles_feed_api import add_article, pipeline, sign_up

DAY = 24 * 60


async def count(db, model) -> int:
    async with db() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_retention_keeps_saved_and_recent_news(api, db) -> None:
    client = api()
    headers = await sign_up(client)
    rbi = await rbi_source(db)
    await add_article(db, rbi, "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance", 5)
    await add_article(db, rbi, "RBI cuts CRR by 50 basis points to 3.5%", 100 * DAY)
    await add_article(db, rbi, "RBI announces OMO purchase of government securities", 120 * DAY)
    await pipeline(db)
    async with db() as session:
        stories = {s.title: s.id for s in await session.scalars(select(Story))}
    assert len(stories) == 3
    # The trader saved one of the old stories: it must survive.
    saved = stories["RBI cuts CRR by 50 basis points to 3.5%"]
    assert (await client.put(f"/api/stories/{saved}/saved", headers=headers)).status_code == 204

    # Housekeeping rows: a sent email, a used link token, an ended session.
    old = datetime.now(UTC) - timedelta(days=40)
    async with db() as session, session.begin():
        await session.execute(update(EmailOutbox).values(status="sent", created_at=old))
        await session.execute(update(EmailToken).values(used_at=old))
        await session.execute(update(UserSession).values(revoked_at=old, revoke_reason="test"))

    report = await run_retention(db)
    async with db() as session:
        left = set(await session.scalars(select(Story.title)))
    assert left == {
        "RBI keeps repo rate unchanged at 5.5%, maintains neutral stance",
        "RBI cuts CRR by 50 basis points to 3.5%",
    }
    assert report.deleted["stories"] == 1 and report.deleted["articles"] == 1
    assert await count(db, Article) == 2
    assert report.deleted["emails"] == 1 and await count(db, EmailOutbox) == 0
    assert report.deleted["email_tokens"] == 1 and await count(db, EmailToken) == 0
    assert report.deleted["sessions"] >= 1

    # Running again finds nothing more to do.
    assert (await run_retention(db)).total == 0


def test_monitoring_strips_personal_data() -> None:
    event = {
        "request": {"url": "https://x/api/auth/login", "data": {"password": "secret"},
                    "cookies": {"nis_refresh": "t"}, "headers": {"Authorization": "Bearer t"}},
        "user": {"id": "u1", "email": "a@b.com", "ip_address": "1.2.3.4"},
    }  # fmt: skip
    scrubbed = _scrub(event, {})
    assert scrubbed["request"] == {"url": "https://x/api/auth/login"}
    assert scrubbed["user"] == {"id": "u1"}


@pytest.mark.parametrize("dsn", [None, ""])
def test_monitoring_is_off_without_a_dsn(dsn, monkeypatch) -> None:
    from app.core import monitoring
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sentry_dsn", dsn)
    monkeypatch.setattr(monitoring, "_started", False)
    assert monitoring.init_monitoring("test") is False
