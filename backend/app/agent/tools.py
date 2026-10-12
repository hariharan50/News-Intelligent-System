"""The agent's tools: read-only searches over stories we already hold.

Stories are shown to the model under short references (S1, S2, ...) that it cites in its
answer; the catalog remembers which story each reference means, so citations become links.
Results only contain headlines, feed excerpts and our own summaries: never scraped text.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import and_, case, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, Profile, Source, Story, StoryScore, StorySummary
from app.models.ai import SummaryStatus
from app.processing.tagger import get_tagger
from app.scoring.engine import tier_of

IST = ZoneInfo("Asia/Kolkata")
MAX_RESULTS = 10
EXCERPT_CHARS = 320

_STOP = frozenset(
    "the a an and or of in on at to for from with by about what whats which who whom how why "
    "when where is are was were be been any anything news latest today week this that these "
    "those there it its into over after before than then have has had did does do happen "
    "happened happening tell me show give update updates on market markets please".split()
)


def _terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9&./-]*", query.lower())
    return [w for w in dict.fromkeys(words) if len(w) >= 3 and w not in _STOP][:8]


def _ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y %H:%M IST")


# --------------------------------------------------------------------------- inputs


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    days: int = Field(default=3, ge=1, le=30)


class TopIn(BaseModel):
    hours: int = Field(default=24, ge=1, le=168)


class StoryIn(BaseModel):
    ref: str = Field(pattern=r"^S\d{1,3}$")


TOOLS = [
    {
        "name": "search_stories",
        "description": (
            "Search the news stories we have collected (headlines, feed excerpts and AI "
            "summaries from RBI, SEBI, the Fed, Indian and global business press). Use short "
            "keyword queries, e.g. 'crude oil', 'HDFC Bank results', 'repo rate'. Returns up to "
            "10 stories, most relevant first, each with a ref like S3 to cite."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, company or topic."},
                "days": {
                    "type": "integer",
                    "description": "How far back to look, 1-30. Default 3.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "top_stories",
        "description": (
            "The most important stories for this user in the last N hours, ranked by their "
            "own profile's score. Use for broad questions like 'what happened today?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"hours": {"type": "integer", "description": "1-168. Default 24."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_story",
        "description": (
            "Everything we hold on one story: every outlet's headline and excerpt, times, "
            "and the AI summary with key facts. Use when a search result looks relevant and "
            "you need its details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "A ref such as S3."}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    },
]


# --------------------------------------------------------------------------- catalog


@dataclass
class CitedStory:
    story_id: uuid.UUID
    title: str
    url: str
    source: str
    published_at: datetime

    def as_citation(self, n: int) -> dict:
        return {
            "n": n,
            "story_id": str(self.story_id),
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
        }


@dataclass
class StoryCatalog:
    db: AsyncSession
    user_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    by_ref: dict[str, CitedStory] = field(default_factory=dict)
    ref_of: dict[uuid.UUID, str] = field(default_factory=dict)

    @classmethod
    async def for_user(cls, db: AsyncSession, user_id: uuid.UUID) -> "StoryCatalog":
        profile_id = await db.scalar(
            select(Profile.id)
            .where(Profile.user_id == user_id)
            .order_by(Profile.created_at)
            .limit(1)
        )
        return cls(db=db, user_id=user_id, profile_id=profile_id)

    def _ref(self, story: Story, source: str) -> str:
        if story.id not in self.ref_of:
            ref = f"S{len(self.ref_of) + 1}"
            self.ref_of[story.id] = ref
            self.by_ref[ref] = CitedStory(
                story.id, story.title, story.url, source, story.first_published_at
            )
        return self.ref_of[story.id]

    async def _describe(self, rows: list[tuple[Story, int | None]]) -> list[dict]:
        if not rows:
            return []
        ids = [s.id for s, _ in rows]
        sources = dict(
            (
                await self.db.execute(
                    select(Source.id, Source.name).where(
                        Source.id.in_({s.lead_source_id for s, _ in rows})
                    )
                )
            ).all()
        )
        summaries = {
            r.story_id: r
            for r in await self.db.scalars(
                select(StorySummary).where(
                    StorySummary.story_id.in_(ids), StorySummary.status == SummaryStatus.OK
                )
            )
        }
        excerpts: dict[uuid.UUID, str] = {}
        for story_id, excerpt in await self.db.execute(
            select(Article.story_id, Article.excerpt)
            .where(Article.story_id.in_(ids), Article.excerpt.is_not(None))
            .order_by(Article.published_at)
        ):
            excerpts.setdefault(story_id, excerpt[:EXCERPT_CHARS])

        out = []
        for story, score in rows:
            source = story.lead_publisher or sources.get(story.lead_source_id, "")
            item = {
                "ref": self._ref(story, source),
                "title": story.title,
                "source": source,
                "published": _ist(story.first_published_at),
                "outlets": story.source_count,
            }
            if story.event_type:
                item["event"] = story.event_type.replace("_", " ")
            if score is not None:
                item["importance_for_user"] = f"{score}/100 ({tier_of(score) or 'low'})"
            if (summary := summaries.get(story.id)) and summary.summary:
                item["summary"] = summary.summary
            elif story.id in excerpts:
                item["excerpt"] = excerpts[story.id]
            out.append(item)
        return out

    def _user_score(self):
        if self.profile_id is None:
            return None
        return and_(StoryScore.story_id == Story.id, StoryScore.profile_id == self.profile_id)

    async def search(self, args: SearchIn) -> dict:
        terms = _terms(args.query)
        entities = get_tagger().entities_in(args.query)
        if not terms and not entities:
            return {"results": [], "note": "Use specific keywords, e.g. a company, asset or event."}
        hits = [Story.match_text.contains(t, autoescape=True) for t in terms]
        rank = sum((case((h, 1), else_=0) for h in hits), start=0)
        if entities:
            entity_hit = Story.entities.overlap(entities)
            hits.append(entity_hit)
            rank = rank + case((entity_hit, 2), else_=0)
        join = self._user_score()
        score_col = StoryScore.score if join is not None else literal(None, StoryScore.score.type)
        query = select(Story, score_col).where(
            Story.last_published_at >= self.now - timedelta(days=args.days), or_(*hits)
        )
        if join is not None:
            query = query.outerjoin(StoryScore, join)
        rows = (
            await self.db.execute(
                query.order_by(
                    rank.desc(), score_col.desc().nulls_last(), Story.last_published_at.desc()
                ).limit(MAX_RESULTS)
            )
        ).all()
        results = await self._describe([tuple(r) for r in rows])
        return {"query": args.query, "days": args.days, "results": results}

    async def top(self, args: TopIn) -> dict:
        cutoff = self.now - timedelta(hours=args.hours)
        if self.profile_id is not None:
            rows = (
                await self.db.execute(
                    select(Story, StoryScore.score)
                    .join(StoryScore, StoryScore.story_id == Story.id)
                    .where(
                        StoryScore.profile_id == self.profile_id,
                        StoryScore.story_published_at >= cutoff,
                    )
                    .order_by(StoryScore.score.desc(), StoryScore.story_published_at.desc())
                    .limit(MAX_RESULTS)
                )
            ).all()
        else:
            rows = [
                (s, None)
                for s in await self.db.scalars(
                    select(Story)
                    .where(Story.first_published_at >= cutoff)
                    .order_by(Story.max_authority.desc(), Story.source_count.desc())
                    .limit(MAX_RESULTS)
                )
            ]
        return {"hours": args.hours, "results": await self._describe([tuple(r) for r in rows])}

    async def story(self, args: StoryIn) -> dict:
        cited = self.by_ref.get(args.ref)
        if cited is None:
            return {"error": f"Unknown ref {args.ref}. Use a ref from a search result."}
        articles = (
            await self.db.execute(
                select(Article.title, Article.excerpt, Article.published_at, Source.name)
                .join(Source, Source.id == Article.source_id)
                .where(Article.story_id == cited.story_id)
                .order_by(Article.published_at)
                .limit(8)
            )
        ).all()
        summary = await self.db.get(StorySummary, cited.story_id)
        out = {
            "ref": args.ref,
            "title": cited.title,
            "reports": [
                {
                    "source": src,
                    "title": title,
                    "published": _ist(published),
                    **({"excerpt": excerpt[:600]} if excerpt else {}),
                }
                for title, excerpt, published, src in articles
            ],
        }
        if summary is not None and summary.status == SummaryStatus.OK and summary.summary:
            out["summary"] = summary.summary
            out["key_facts"] = summary.key_facts
        return out

    async def run(self, name: str, raw_input: object) -> tuple[str, bool]:
        """Execute one tool call. Returns (JSON result, is_error)."""
        try:
            match name:
                case "search_stories":
                    result = await self.search(SearchIn.model_validate(raw_input))
                case "top_stories":
                    result = await self.top(TopIn.model_validate(raw_input or {}))
                case "get_story":
                    result = await self.story(StoryIn.model_validate(raw_input))
                case _:
                    return json.dumps({"error": f"No tool named {name}."}), True
        except ValidationError as exc:
            return json.dumps(
                {"error": "Invalid input", "details": exc.errors(include_url=False)}, default=str
            ), True
        return json.dumps(result, ensure_ascii=False), False
