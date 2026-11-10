"""session persistence flag

Marks whether a login asked to be kept signed in. Existing sessions default to false, so
after this deploys every browser that was relying on the old 30-day cookie must sign in
again once the cookie it already holds is used up.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-10 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "persistent", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "persistent")
