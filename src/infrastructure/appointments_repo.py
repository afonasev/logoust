from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    delete,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.appointment import Appointment
from src.infrastructure.db import Base


class AppointmentORM(Base):
    __tablename__ = "appointments"
    # Feeds are always filtered by owner (or client) and ordered by start time;
    # both composite indexes serve their range scans by left-prefix.
    __table_args__ = (
        Index("ix_appointments_specialist_starts", "specialist_id", "starts_at"),
        Index("ix_appointments_client_starts", "client_id", "starts_at"),
        # Idempotency anchor for settle's insert-or-ignore of past occurrences.
        Index(
            "uq_appointments_series_origin",
            "series_id",
            "origin_date",
            unique=True,
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
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set together on series occurrences (NULL on one-off appointments).
    series_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("recurring_appointments.id"), nullable=True
    )
    origin_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<AppointmentORM id={self.id} client={self.client_id} "
            f"starts_at={self.starts_at}>"
        )


def _as_utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it. Without
    # this, wall-clock conversion (utc_to_wall) would treat the value as local
    # time and shift appointments by the deploy machine's offset.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def to_domain(orm: AppointmentORM) -> Appointment:
    return Appointment(
        id=orm.id,
        specialist_id=orm.specialist_id,
        client_id=orm.client_id,
        starts_at=_as_utc(orm.starts_at),
        comment=orm.comment,
        created_at=_as_utc(orm.created_at),
        updated_at=_as_utc(orm.updated_at),
        series_id=orm.series_id,
        origin_date=orm.origin_date,
    )


class SqlAlchemyAppointmentsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, appointment: Appointment) -> Appointment:
        orm = AppointmentORM(
            specialist_id=appointment.specialist_id,
            client_id=appointment.client_id,
            starts_at=appointment.starts_at,
            comment=appointment.comment,
            created_at=appointment.created_at,
            updated_at=appointment.updated_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def get_for_specialist(
        self, appointment_id: int, specialist_id: int
    ) -> Appointment | None:
        orm = await self._get_owned(appointment_id, specialist_id)
        return to_domain(orm) if orm is not None else None

    async def list_future_for_specialist(
        self, specialist_id: int, *, since: datetime
    ) -> list[Appointment]:
        stmt = (
            select(AppointmentORM)
            .where(
                AppointmentORM.specialist_id == specialist_id,
                AppointmentORM.starts_at >= since,
            )
            .order_by(AppointmentORM.starts_at.asc())
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_for_specialist_between(
        self, specialist_id: int, *, start: datetime, end: datetime
    ) -> list[Appointment]:
        stmt = (
            select(AppointmentORM)
            .where(
                AppointmentORM.specialist_id == specialist_id,
                AppointmentORM.starts_at >= start,
                AppointmentORM.starts_at < end,
            )
            .order_by(AppointmentORM.starts_at.asc())
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_past_for_specialist(
        self, specialist_id: int, *, before: datetime, limit: int, offset: int
    ) -> list[Appointment]:
        stmt = (
            select(AppointmentORM)
            .where(
                AppointmentORM.specialist_id == specialist_id,
                AppointmentORM.starts_at < before,
            )
            .order_by(AppointmentORM.starts_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_future_for_client(
        self, specialist_id: int, client_id: int, *, since: datetime
    ) -> list[Appointment]:
        stmt = (
            select(AppointmentORM)
            .where(
                AppointmentORM.specialist_id == specialist_id,
                AppointmentORM.client_id == client_id,
                AppointmentORM.starts_at >= since,
            )
            .order_by(AppointmentORM.starts_at.asc())
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_past_for_client(
        self,
        specialist_id: int,
        client_id: int,
        *,
        before: datetime,
        limit: int,
        offset: int,
    ) -> list[Appointment]:
        stmt = (
            select(AppointmentORM)
            .where(
                AppointmentORM.specialist_id == specialist_id,
                AppointmentORM.client_id == client_id,
                AppointmentORM.starts_at < before,
            )
            .order_by(AppointmentORM.starts_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def update_starts_at(
        self,
        appointment_id: int,
        specialist_id: int,
        *,
        starts_at: datetime,
        updated_at: datetime,
    ) -> Appointment | None:
        orm = await self._get_owned(appointment_id, specialist_id)
        if orm is None:
            return None
        orm.starts_at = starts_at
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def insert_occurrence(self, occurrence: Appointment) -> bool:
        # Insert-or-ignore on UNIQUE(series_id, origin_date): settle may run
        # concurrently or repeatedly, so a duplicate occurrence is a no-op, not an
        # error. Returns True when a new row was actually written.
        stmt = (
            sqlite_insert(AppointmentORM)
            .values(
                specialist_id=occurrence.specialist_id,
                client_id=occurrence.client_id,
                starts_at=occurrence.starts_at,
                comment=occurrence.comment,
                series_id=occurrence.series_id,
                origin_date=occurrence.origin_date,
                created_at=occurrence.created_at,
                updated_at=occurrence.updated_at,
            )
            .on_conflict_do_nothing(index_elements=["series_id", "origin_date"])
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0

    async def delete(self, appointment_id: int, specialist_id: int) -> bool:
        stmt = delete(AppointmentORM).where(
            AppointmentORM.id == appointment_id,
            AppointmentORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return result.rowcount > 0

    async def _get_owned(
        self, appointment_id: int, specialist_id: int
    ) -> AppointmentORM | None:
        stmt = select(AppointmentORM).where(
            AppointmentORM.id == appointment_id,
            AppointmentORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
