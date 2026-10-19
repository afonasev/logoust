"""specialist keyboard version: track last delivered reply-keyboard hash

Adds the nullable ``specialists.keyboard_version`` column — the hash of the last
reply-keyboard button set delivered to the specialist. No backfill: existing rows
stay ``NULL`` and are correctly treated as "stale", so the first action after the
release delivers the fresh keyboard and records its version.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "specialists",
        sa.Column("keyboard_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("specialists", "keyboard_version")
