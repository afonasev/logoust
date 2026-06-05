"""clients: telegram link columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Существующие клиенты получают NULL — валидное состояние «ещё не приглашён».
    op.add_column(
        "clients", sa.Column("invite_token", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "clients", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("clients", sa.Column("linked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_clients_invite_token", "clients", ["invite_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_clients_invite_token", table_name="clients")
    op.drop_column("clients", "linked_at")
    op.drop_column("clients", "telegram_chat_id")
    op.drop_column("clients", "invite_token")
