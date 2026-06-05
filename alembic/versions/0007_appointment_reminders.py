"""appointment reminders: journal table and specialist reminder settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Reminders are opt-out: existing specialists backfill to enabled at 12:00.
    op.add_column(
        "specialists",
        sa.Column(
            "reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "reminder_time",
            sa.String(length=5),
            nullable=False,
            server_default="12:00",
        ),
    )
    op.add_column(
        "specialists",
        sa.Column("reminder_last_run_on", sa.Date(), nullable=True),
    )
    op.create_table(
        "appointment_reminders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        # NULL on both = one-off appointment; set = a (possibly virtual) series row.
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("origin_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        # Idempotency anchor for the daily insert-or-ignore; also the read index for
        # status lookups by occurrence (specialist_id, client_id left-prefix).
        sa.UniqueConstraint(
            "specialist_id", "client_id", "starts_at", name="uq_reminder_occurrence"
        ),
    )


def downgrade() -> None:
    op.drop_table("appointment_reminders")
    op.drop_column("specialists", "reminder_last_run_on")
    op.drop_column("specialists", "reminder_time")
    op.drop_column("specialists", "reminder_enabled")
