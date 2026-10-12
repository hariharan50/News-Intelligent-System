"""Ask the news (streamed answers with citations) and the daily brief."""

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.agent import service as agent
from app.agent.brief import latest_brief
from app.auth.deps import CurrentUserDep
from app.cache.redis import redis_client
from app.db.session import SessionDep, SessionLocal

router = APIRouter(tags=["agent"])


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    conversation_id: uuid.UUID | None = None

    @field_validator("question")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("Ask a question.")
        return value


def _sse(event: dict) -> str:
    kind = event.pop("type")
    return f"event: {kind}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/agent")
async def agent_status(current: CurrentUserDep, db: SessionDep) -> dict:
    """What the user may do today, and their recent questions."""
    status = await agent.status_for(db, redis_client, current.user_id)
    return {**status, "recent": await agent.recent_questions(db, current.user_id)}


@router.post("/agent/ask", response_class=StreamingResponse)
async def ask(body: AskIn, current: CurrentUserDep, db: SessionDep) -> StreamingResponse:
    """Answer a question as Server-Sent Events: status, reset, delta, done or error.

    Limits are checked before the stream starts, so they come back as ordinary JSON errors.
    """
    await agent.check_allowed(db, redis_client, current.user_id)

    async def events() -> AsyncIterator[str]:
        async for event in agent.ask(
            SessionLocal, redis_client, user_id=current.user_id, question=body.question,
            conversation_id=body.conversation_id,
        ):  # fmt: skip
            yield _sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/briefs/latest")
async def brief(current: CurrentUserDep, db: SessionDep) -> dict | None:
    row = await latest_brief(db, current.user_id)
    if row is None:
        return None
    return {
        "id": str(row.id), "date": row.brief_date.isoformat(), "content": row.content,
        "citations": row.citations, "story_count": row.story_count,
        "created_at": row.created_at.isoformat(),
    }  # fmt: skip
