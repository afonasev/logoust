"""message_templates: per-specialist overrides of client message texts

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No data is seeded: a missing row means "render the default from messages.toml".
    op.create_table(
        "message_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.UniqueConstraint(
            "specialist_id", "template_key", name="uq_message_template_key"
        ),
    )


def downgrade() -> None:
    op.drop_table("message_templates")
