"""recurring appointments: series, exceptions, and appointment back-references

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_appointments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("time_hhmm", sa.String(length=5), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("materialized_through", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
    )
    op.create_index(
        "ix_recurring_specialist_active",
        "recurring_appointments",
        ["specialist_id", "active"],
    )
    op.create_table(
        "recurring_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("series_id", sa.Integer(), nullable=False),
        sa.Column("original_date", sa.Date(), nullable=False),
        # NULL = skip the date; a value = move the occurrence to this UTC instant.
        sa.Column("new_starts_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["recurring_appointments.id"]),
        sa.UniqueConstraint(
            "series_id", "original_date", name="uq_exception_series_date"
        ),
    )
    # Back-references on appointments: NULL on both = a one-off appointment (as
    # before). The unique index makes settle's insert-or-ignore idempotent. The
    # series_id → recurring_appointments FK lives in the ORM only: SQLite does not
    # enforce FKs and ALTERing one in needs batch table-rebuild, so we skip it here.
    op.add_column("appointments", sa.Column("series_id", sa.Integer(), nullable=True))
    op.add_column("appointments", sa.Column("origin_date", sa.Date(), nullable=True))
    op.create_index(
        "uq_appointments_series_origin",
        "appointments",
        ["series_id", "origin_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_appointments_series_origin", table_name="appointments")
    op.drop_column("appointments", "origin_date")
    op.drop_column("appointments", "series_id")
    op.drop_table("recurring_exceptions")
    op.drop_index("ix_recurring_specialist_active", table_name="recurring_appointments")
    op.drop_table("recurring_appointments")
