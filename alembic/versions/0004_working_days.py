"""specialists: working_days column

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills existing specialists with Mon-Fri working days.
    op.add_column(
        "specialists",
        sa.Column(
            "working_days",
            sa.String(length=20),
            nullable=False,
            server_default="0,1,2,3,4",
        ),
    )


def downgrade() -> None:
    op.drop_column("specialists", "working_days")
