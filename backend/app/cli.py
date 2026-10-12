"""Operational commands. Usage: python -m app.cli <command> [argument]

seed-sources         insert launch news sources that don't exist yet (safe to re-run)
process              tag and cluster all unprocessed articles now
reprocess            forget all stories and tags, then rebuild them (after taxonomy changes)
rescore              re-score every story for every profile (after scoring changes)
remove-google-news   delete every Google News source and its articles, rebuild stories
make-admin <email>   give an existing account administrator rights
"""

import asyncio
import sys

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal, engine
from app.ingestion.seed import seed_sources
from app.models import Story, User
from app.processing.service import process_all_pending, reset_processing
from app.scoring.service import score_unscored


async def _seed_sources() -> None:
    added = await seed_sources(SessionLocal)
    print(f"seed-sources: {added} new source(s) added")


async def _process() -> None:
    result = await process_all_pending(SessionLocal, max_batches=10_000)
    print(
        f"process: {result.articles} article(s), {result.new_stories} new story(ies), "
        f"{result.updated_stories} update(s)"
    )
    scored = await score_unscored(SessionLocal, limit=100_000, announce=False)
    print(f"process: scored {scored} story(ies) for matching profiles")


async def _reprocess() -> None:
    reset = await reset_processing(SessionLocal)
    print(f"reprocess: reset {reset} article(s)")
    await _process()


async def _rescore() -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(update(Story).values(scored_at=None))
    scored = await score_unscored(SessionLocal, limit=100_000, announce=False)
    print(f"rescore: scored {scored} story(ies) for matching profiles")


async def _remove_google_news() -> None:
    """Delete all Google News sources (their articles go with them), then rebuild the stories
    they contributed to: stories left with no articles are deleted."""
    from sqlalchemy import delete as sql_delete

    from app.models import Article, Source, SourceKind
    from app.processing.service import refresh_stories

    async with SessionLocal() as session, session.begin():
        google = select(Source.id).where(Source.kind == SourceKind.GOOGLE_NEWS)
        story_ids = set(
            await session.scalars(
                select(Article.story_id)
                .where(Article.source_id.in_(google), Article.story_id.is_not(None))
                .distinct()
            )
        )
        articles = await session.scalar(
            select(func.count()).select_from(Article).where(Article.source_id.in_(google))
        )
        sources = (
            await session.execute(sql_delete(Source).where(Source.kind == SourceKind.GOOGLE_NEWS))
        ).rowcount
        await session.flush()
        await refresh_stories(session, story_ids)
    print(f"remove-google-news: deleted {sources} source(s) and {articles} article(s); "
          f"rebuilt {len(story_ids)} story(ies)")  # fmt: skip
    await _rescore()


async def _make_admin(email: str) -> None:
    async with SessionLocal() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == email.strip()))
        if user is None:
            raise SystemExit(f"make-admin: no account with email {email!r} (sign up first)")
        user.is_admin = True
    print(f"make-admin: {email} is now an administrator")


COMMANDS = {
    "seed-sources": (_seed_sources, 0),
    "process": (_process, 0),
    "reprocess": (_reprocess, 0),
    "rescore": (_rescore, 0),
    "remove-google-news": (_remove_google_news, 0),
    "make-admin": (_make_admin, 1),
}


async def _run(command: str, args: list[str]) -> None:
    try:
        await COMMANDS[command][0](*args)
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    configure_logging(get_settings().log_level)
    if not argv or argv[0] not in COMMANDS or len(argv) - 1 != COMMANDS[argv[0]][1]:
        print(__doc__)
        return 2
    asyncio.run(_run(argv[0], argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
