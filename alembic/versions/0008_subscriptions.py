"""subscriptions: table and specialist default-meetings setting

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing specialists backfill to the start default of 8 meetings.
    op.add_column(
        "specialists",
        sa.Column(
            "subscription_default",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("purchased", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
    )
    op.create_index(
        "ix_subscriptions_client_status", "subscriptions", ["client_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_client_status", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_column("specialists", "subscription_default")
