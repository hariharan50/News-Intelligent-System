import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StoryUserState(Base):
    """Per-user read/saved state. Independent of profiles: reading a story in one profile's
    feed marks it read everywhere. Saved stories are kept while the account exists."""

    __tablename__ = "story_user_state"
    __table_args__ = (
        Index(
            "ix_story_user_state_saved",
            "user_id",
            text("saved_at DESC"),
            postgresql_where=text("saved_at IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
