"""subscription presets: replace the single default-meetings setting with a list

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The single `subscription_default` (one number) is replaced by a list of
    # preset variants shown as buttons. Existing specialists get the standard
    # "4,8,12"; the old per-specialist number is not carried over (it was only a
    # convenience default, superseded by the list).
    op.add_column(
        "specialists",
        sa.Column(
            "subscription_presets",
            sa.String(length=64),
            nullable=False,
            server_default="4,8,12",
        ),
    )
    op.drop_column("specialists", "subscription_default")


def downgrade() -> None:
    op.add_column(
        "specialists",
        sa.Column(
            "subscription_default",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("8"),
        ),
    )
    op.drop_column("specialists", "subscription_presets")
