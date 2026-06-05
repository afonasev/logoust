"""appointments: appointments table + specialist schedule settings

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
    )
    op.create_index(
        "ix_appointments_specialist_starts",
        "appointments",
        ["specialist_id", "starts_at"],
    )
    op.create_index(
        "ix_appointments_client_starts",
        "appointments",
        ["client_id", "starts_at"],
    )
    # server_default backfills existing specialists with a working schedule.
    op.add_column(
        "specialists",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Asia/Yekaterinburg",
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "day_start", sa.String(length=5), nullable=False, server_default="09:00"
        ),
    )
    op.add_column(
        "specialists",
        sa.Column(
            "day_end", sa.String(length=5), nullable=False, server_default="20:00"
        ),
    )
    op.add_column(
        "specialists",
        sa.Column("slot_minutes", sa.Integer(), nullable=False, server_default="60"),
    )


def downgrade() -> None:
    op.drop_column("specialists", "slot_minutes")
    op.drop_column("specialists", "day_end")
    op.drop_column("specialists", "day_start")
    op.drop_column("specialists", "timezone")
    op.drop_index("ix_appointments_client_starts", table_name="appointments")
    op.drop_index("ix_appointments_specialist_starts", table_name="appointments")
    op.drop_table("appointments")
