"""multi-slot recurring: schedules own slots, slots own overrides

Replaces the single-rule recurring schema (recurring_appointments +
recurring_exceptions) with a client *schedule* owning many *slots*, each slot
owning per-date *overrides* (skip / move / comment). The appointment and reminder
back-reference column `series_id` becomes `slot_id`. Recurring data is wiped — prod
is empty (see design.md, decision 7); one-off appointments (series_id IS NULL) are
kept.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Wipe materialised recurring rows and their reminders; the recurring schema is
    # replaced wholesale. One-off rows (series_id IS NULL) survive untouched.
    op.execute("DELETE FROM appointments WHERE series_id IS NOT NULL")
    op.execute("DELETE FROM appointment_reminders WHERE series_id IS NOT NULL")

    op.drop_index("uq_appointments_series_origin", table_name="appointments")
    op.drop_table("recurring_exceptions")
    op.drop_index("ix_recurring_specialist_active", table_name="recurring_appointments")
    op.drop_table("recurring_appointments")

    # Rename the back-reference column on both journals; identity is now per slot.
    op.alter_column("appointments", "series_id", new_column_name="slot_id")
    op.alter_column("appointment_reminders", "series_id", new_column_name="slot_id")
    op.create_index(
        "uq_appointments_slot_origin",
        "appointments",
        ["slot_id", "origin_date"],
        unique=True,
    )

    op.create_table(
        "recurring_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("specialist_id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["specialist_id"], ["specialists.id"]),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
    )
    op.create_index(
        "ix_recurring_schedules_specialist_active",
        "recurring_schedules",
        ["specialist_id", "active"],
    )
    op.create_table(
        "recurring_slots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("time_hhmm", sa.String(length=5), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("materialized_through", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["recurring_schedules.id"]),
    )
    op.create_index(
        "ix_recurring_slots_schedule_active",
        "recurring_slots",
        ["schedule_id", "active"],
    )
    op.create_table(
        "recurring_slot_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("original_date", sa.Date(), nullable=False),
        sa.Column("skipped", sa.Boolean(), nullable=False),
        # set = move the occurrence to this UTC instant; NULL = the grid time.
        sa.Column("moved_to", sa.DateTime(), nullable=True),
        # set = overrides the schedule comment for this occurrence; NULL = inherit.
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["slot_id"], ["recurring_slots.id"]),
        sa.UniqueConstraint("slot_id", "original_date", name="uq_override_slot_date"),
    )


def downgrade() -> None:
    op.drop_table("recurring_slot_overrides")
    op.drop_index("ix_recurring_slots_schedule_active", table_name="recurring_slots")
    op.drop_table("recurring_slots")
    op.drop_index(
        "ix_recurring_schedules_specialist_active", table_name="recurring_schedules"
    )
    op.drop_table("recurring_schedules")

    op.drop_index("uq_appointments_slot_origin", table_name="appointments")
    op.alter_column("appointments", "slot_id", new_column_name="series_id")
    op.alter_column("appointment_reminders", "slot_id", new_column_name="series_id")
    op.create_index(
        "uq_appointments_series_origin",
        "appointments",
        ["series_id", "origin_date"],
        unique=True,
    )

    # Restore the prior recurring schema (without data — prod is empty).
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
        sa.Column("new_starts_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["recurring_appointments.id"]),
        sa.UniqueConstraint(
            "series_id", "original_date", name="uq_exception_series_date"
        ),
    )
