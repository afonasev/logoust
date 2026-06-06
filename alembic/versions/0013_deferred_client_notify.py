"""deferred client notify: outbox queue + preset-time setting

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing specialists backfill to the 20:00 preset (the default deferred time).
    op.add_column(
        "specialists",
        sa.Column(
            "deferred_notify_time",
            sa.String(length=5),
            nullable=False,
            server_default="20:00",
        ),
    )
    op.create_table(
        "scheduled_client_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
    )
    op.create_index(
        "ix_scheduled_status_due",
        "scheduled_client_messages",
        ["status", "due_at"],
    )
    op.create_index(
        "ix_scheduled_specialist_client_status",
        "scheduled_client_messages",
        ["specialist_id", "client_id", "status"],
    )
    op.create_index(
        "ix_scheduled_specialist_target_status",
        "scheduled_client_messages",
        ["specialist_id", "target_key", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_specialist_target_status",
        table_name="scheduled_client_messages",
    )
    op.drop_index(
        "ix_scheduled_specialist_client_status",
        table_name="scheduled_client_messages",
    )
    op.drop_index("ix_scheduled_status_due", table_name="scheduled_client_messages")
    op.drop_table("scheduled_client_messages")
    op.drop_column("specialists", "deferred_notify_time")
