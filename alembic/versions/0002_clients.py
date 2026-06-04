"""clients: clients table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("child_name", sa.String(length=200), nullable=False),
        sa.Column("contact_name", sa.String(length=200), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=True),
        sa.Column("contact_telegram", sa.String(length=64), nullable=True),
        sa.Column("extra_contacts", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
    )
    op.create_index(
        "ix_clients_specialist_status",
        "clients",
        ["specialist_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_clients_specialist_status", table_name="clients")
    op.drop_table("clients")
