"""subscription payment reminder: specialist settings + per-subscription flag

Adds the payment-reminder settings to ``specialists`` (opt-out, noon wall-time) and
the per-subscription anti-duplicate flag ``payment_reminded_at`` to
``subscriptions``. Existing specialists backfill to enabled at 12:00; existing
subscriptions get a NULL flag.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The payment reminder is opt-out: existing specialists backfill to enabled at
    # 12:00 wall-time, with no run recorded yet.
    op.add_column(
        "specialists",
        sa.Column(
            "payment_reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "payment_reminder_time",
            sa.String(length=5),
            nullable=False,
            server_default="12:00",
        ),
    )
    op.add_column(
        "specialists",
        sa.Column("payment_reminder_last_run_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("payment_reminded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "payment_reminded_at")
    op.drop_column("specialists", "payment_reminder_last_run_on")
    op.drop_column("specialists", "payment_reminder_time")
    op.drop_column("specialists", "payment_reminder_enabled")
