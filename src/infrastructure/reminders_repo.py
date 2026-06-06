from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.infrastructure.db import Base


def _as_utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it (mirrors
    # appointments_repo._as_utc) — else wall-clock conversion shifts by the host's
    # offset and occurrence keys stop matching.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_utc_opt(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


class AppointmentReminderORM(Base):
    __tablename__ = "appointment_reminders"
    # The UNIQUE key both makes the daily insert idempotent (ON CONFLICT DO NOTHING)
    # and serves status reads by occurrence (left-prefix specialist_id, client_id).
    __table_args__ = (
        UniqueConstraint(
            "specialist_id", "client_id", "starts_at", name="uq_reminder_occurrence"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AppointmentReminderORM id={self.id} client={self.client_id} "
            f"starts_at={self.starts_at} status={self.status}>"
        )


def to_domain(orm: AppointmentReminderORM) -> AppointmentReminder:
    return AppointmentReminder(
        id=orm.id,
        specialist_id=orm.specialist_id,
        client_id=orm.client_id,
        starts_at=_as_utc(orm.starts_at),
        slot_id=orm.slot_id,
        origin_date=orm.origin_date,
        status=ReminderStatus(orm.status),
        sent_at=_as_utc(orm.sent_at),
        responded_at=_as_utc_opt(orm.responded_at),
    )


class SqlAlchemyRemindersRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_pending(self, reminder: AppointmentReminder) -> bool:
        # Insert-or-ignore on UNIQUE(specialist_id, client_id, starts_at): a repeat
        # tick or restart must not re-send. Returns True only when a new row landed;
        # on success the generated id is written back so the caller can build the
        # confirm/decline callback_data.
        stmt = (
            sqlite_insert(AppointmentReminderORM)
            .values(
                specialist_id=reminder.specialist_id,
                client_id=reminder.client_id,
                starts_at=reminder.starts_at,
                slot_id=reminder.slot_id,
                origin_date=reminder.origin_date,
                status=reminder.status.value,
                sent_at=reminder.sent_at,
                responded_at=reminder.responded_at,
            )
            .on_conflict_do_nothing(
                index_elements=["specialist_id", "client_id", "starts_at"]
            )
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        if result.rowcount <= 0:
            return False
        loaded = await self._session.execute(
            select(AppointmentReminderORM.id).where(
                AppointmentReminderORM.specialist_id == reminder.specialist_id,
                AppointmentReminderORM.client_id == reminder.client_id,
                AppointmentReminderORM.starts_at == reminder.starts_at,
            )
        )
        reminder.id = loaded.scalar_one()
        return True

    async def get(self, reminder_id: int) -> AppointmentReminder | None:
        orm = await self._session.get(AppointmentReminderORM, reminder_id)
        return to_domain(orm) if orm is not None else None

    async def set_status(
        self, reminder_id: int, status: ReminderStatus, responded_at: datetime
    ) -> ReminderStatus | None:
        orm = await self._session.get(AppointmentReminderORM, reminder_id)
        if orm is None:  # pragma: no cover - callers load the reminder first
            return None
        previous = ReminderStatus(orm.status)
        orm.status = status.value
        orm.responded_at = responded_at
        await self._session.commit()
        return previous

    async def statuses_for_day(
        self,
        specialist_id: int,
        occurrences: list[tuple[int, datetime]],
    ) -> dict[tuple[int, datetime], ReminderStatus]:
        """Statuses keyed by `(client_id, starts_at)` for the given occurrences."""
        if not occurrences:
            return {}
        client_ids = {client_id for client_id, _ in occurrences}
        stmt = select(AppointmentReminderORM).where(
            AppointmentReminderORM.specialist_id == specialist_id,
            AppointmentReminderORM.client_id.in_(client_ids),
        )
        result = await self._session.execute(stmt)
        wanted = set(occurrences)
        statuses: dict[tuple[int, datetime], ReminderStatus] = {}
        for orm in result.scalars().all():
            key = (orm.client_id, _as_utc(orm.starts_at))
            if key in wanted:
                statuses[key] = ReminderStatus(orm.status)
        return statuses
