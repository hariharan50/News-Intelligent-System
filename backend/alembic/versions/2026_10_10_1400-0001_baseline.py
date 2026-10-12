"""baseline: database extensions

Revision ID: 0001
Revises:
Create Date: 2026-10-10 14:00:00

pg_trgm  - trigram similarity for near-duplicate headline detection (Phase 2) and search.
citext   - case-insensitive text for user emails (Phase 3).
pgcrypto - gen_random_uuid() for primary keys.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "citext", "pgcrypto")


def upgrade() -> None:
    for ext in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")


def downgrade() -> None:
    for ext in reversed(EXTENSIONS):
        op.execute(f"DROP EXTENSION IF EXISTS {ext}")
