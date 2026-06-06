"""morning digest: specialist daily-summary settings

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The morning digest is opt-out: existing specialists backfill to enabled at
    # 10:00 wall-time, with no run recorded yet.
    op.add_column(
        "specialists",
        sa.Column(
            "morning_notify_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "morning_notify_time",
            sa.String(length=5),
            nullable=False,
            server_default="10:00",
        ),
    )
    op.add_column(
        "specialists",
        sa.Column("morning_notify_last_run_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("specialists", "morning_notify_last_run_on")
    op.drop_column("specialists", "morning_notify_time")
    op.drop_column("specialists", "morning_notify_enabled")
