"""initial: specialists table

Revision ID: 0001
Revises:
Create Date: 2026-05-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "specialists",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("invite_token", sa.String(length=64), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("welcomed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_specialists_invite_token",
        "specialists",
        ["invite_token"],
        unique=True,
    )
    op.create_index(
        "ix_specialists_telegram_chat_id",
        "specialists",
        ["telegram_chat_id"],
        unique=True,
        sqlite_where=sa.text("telegram_chat_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_specialists_telegram_chat_id", table_name="specialists")
    op.drop_index("ix_specialists_invite_token", table_name="specialists")
    op.drop_table("specialists")
