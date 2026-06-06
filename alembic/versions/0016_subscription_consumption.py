"""subscription consumption: deduction journal + evening-pass settings

Adds the ``subscription_deductions`` journal (with a partial unique index on
``appointment_id`` as the per-meeting idempotency lock) and the evening
consumption-pass settings to ``specialists`` (opt-out, 20:00 wall-time). Existing
specialists backfill to enabled at 20:00.

The idempotent materialisation of a repeat (design.md, решение 3) relies on the
unique index ``uq_appointments_slot_origin`` on ``appointments(slot_id,
origin_date)`` — that index already exists since 0014, so no change is needed here.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subscription_deductions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("subscriptions.id"),
            nullable=False,
        ),
        sa.Column(
            "appointment_id",
            sa.Integer(),
            sa.ForeignKey("appointments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("appointment_starts_at", sa.DateTime(), nullable=True),
        sa.Column("appointment_comment", sa.Text(), nullable=True),
        sa.Column("closing_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    )
    # Partial unique index = the per-meeting idempotency lock; manual deductions
    # (appointment_id IS NULL) are excluded so they never collide (решение 1).
    op.create_index(
        "uq_subscription_deductions_appointment",
        "subscription_deductions",
        ["appointment_id"],
        unique=True,
        sqlite_where=sa.text("appointment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_subscription_deductions_subscription",
        "subscription_deductions",
        ["subscription_id"],
    )

    # The consumption pass is opt-out: existing specialists backfill to enabled at
    # 20:00 wall-time, with no run recorded yet.
    op.add_column(
        "specialists",
        sa.Column(
            "consumption_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "consumption_time",
            sa.String(length=5),
            nullable=False,
            server_default="20:00",
        ),
    )
    op.add_column(
        "specialists",
        sa.Column("consumption_last_run_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("specialists", "consumption_last_run_on")
    op.drop_column("specialists", "consumption_time")
    op.drop_column("specialists", "consumption_enabled")
    op.drop_index(
        "ix_subscription_deductions_subscription",
        table_name="subscription_deductions",
    )
    op.drop_index(
        "uq_subscription_deductions_appointment",
        table_name="subscription_deductions",
    )
    op.drop_table("subscription_deductions")
