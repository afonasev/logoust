from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.recurring import RecurringAppointment, RecurringException
from src.infrastructure.db import Base


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it (mirrors
    # appointments_repo._as_utc). None stays None — a skip exception has no time.
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RecurringAppointmentORM(Base):
    __tablename__ = "recurring_appointments"
    # Settle and read-merge always filter by owner and activeness.
    __table_args__ = (
        Index("ix_recurring_specialist_active", "specialist_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    time_hhmm: Mapped[str] = mapped_column(String(5), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    materialized_through: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<RecurringAppointmentORM id={self.id} client={self.client_id} "
            f"weekday={self.weekday} time={self.time_hhmm} active={self.active}>"
        )


class RecurringExceptionORM(Base):
    __tablename__ = "recurring_exceptions"
    __table_args__ = (
        UniqueConstraint("series_id", "original_date", name="uq_exception_series_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recurring_appointments.id"), nullable=False
    )
    original_date: Mapped[date] = mapped_column(Date, nullable=False)
    new_starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<RecurringExceptionORM id={self.id} series={self.series_id} "
            f"date={self.original_date} moved={self.new_starts_at is not None}>"
        )


def to_domain(orm: RecurringAppointmentORM) -> RecurringAppointment:
    return RecurringAppointment(
        id=orm.id,
        specialist_id=orm.specialist_id,
        client_id=orm.client_id,
        weekday=orm.weekday,
        time_hhmm=orm.time_hhmm,
        comment=orm.comment,
        active=bool(orm.active),
        start_date=orm.start_date,
        materialized_through=orm.materialized_through,
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type]
        updated_at=_as_utc(orm.updated_at),  # type: ignore[arg-type]
    )


def exception_to_domain(orm: RecurringExceptionORM) -> RecurringException:
    return RecurringException(
        id=orm.id,
        series_id=orm.series_id,
        original_date=orm.original_date,
        new_starts_at=_as_utc(orm.new_starts_at),
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type]
    )


class SqlAlchemyRecurringRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, series: RecurringAppointment) -> RecurringAppointment:
        orm = RecurringAppointmentORM(
            specialist_id=series.specialist_id,
            client_id=series.client_id,
            weekday=series.weekday,
            time_hhmm=series.time_hhmm,
            comment=series.comment,
            active=series.active,
            start_date=series.start_date,
            materialized_through=series.materialized_through,
            created_at=series.created_at,
            updated_at=series.updated_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def list_active_for_specialist(
        self, specialist_id: int
    ) -> list[RecurringAppointment]:
        stmt = (
            select(RecurringAppointmentORM)
            .where(
                RecurringAppointmentORM.specialist_id == specialist_id,
                RecurringAppointmentORM.active.is_(True),
            )
            .order_by(RecurringAppointmentORM.id.asc())
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def get_for_specialist(
        self, series_id: int, specialist_id: int
    ) -> RecurringAppointment | None:
        orm = await self._get_owned(series_id, specialist_id)
        return to_domain(orm) if orm is not None else None

    async def set_active(
        self, series_id: int, specialist_id: int, *, active: bool, updated_at: datetime
    ) -> RecurringAppointment | None:
        orm = await self._get_owned(series_id, specialist_id)
        if orm is None:
            return None
        orm.active = active
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def set_materialized_through(
        self, series_id: int, *, materialized_through: date
    ) -> None:
        orm = await self._session.get(RecurringAppointmentORM, series_id)
        if orm is None:  # pragma: no cover - settle only passes loaded series ids
            return
        orm.materialized_through = materialized_through
        await self._session.commit()

    async def update_rule(  # noqa: PLR0913
        self,
        series_id: int,
        specialist_id: int,
        *,
        weekday: int,
        time_hhmm: str,
        comment: str | None,
        start_date: date,
        materialized_through: date,
        updated_at: datetime,
    ) -> RecurringAppointment | None:
        orm = await self._get_owned(series_id, specialist_id)
        if orm is None:
            return None
        orm.weekday = weekday
        orm.time_hhmm = time_hhmm
        orm.comment = comment
        orm.start_date = start_date
        orm.materialized_through = materialized_through
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def _get_owned(
        self, series_id: int, specialist_id: int
    ) -> RecurringAppointmentORM | None:
        stmt = select(RecurringAppointmentORM).where(
            RecurringAppointmentORM.id == series_id,
            RecurringAppointmentORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SqlAlchemyRecurringExceptionsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        series_id: int,
        original_date: date,
        *,
        new_starts_at: datetime | None,
        created_at: datetime,
    ) -> RecurringException:
        # Skip and move both write one row keyed by (series_id, original_date);
        # re-skipping or re-moving the same date overwrites new_starts_at.
        stmt = (
            sqlite_insert(RecurringExceptionORM)
            .values(
                series_id=series_id,
                original_date=original_date,
                new_starts_at=new_starts_at,
                created_at=created_at,
            )
            .on_conflict_do_update(
                index_elements=["series_id", "original_date"],
                set_={"new_starts_at": new_starts_at},
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()
        loaded = await self._session.execute(
            select(RecurringExceptionORM).where(
                RecurringExceptionORM.series_id == series_id,
                RecurringExceptionORM.original_date == original_date,
            )
        )
        return exception_to_domain(loaded.scalar_one())

    async def list_for_series(self, series_id: int) -> list[RecurringException]:
        stmt = select(RecurringExceptionORM).where(
            RecurringExceptionORM.series_id == series_id
        )
        result = await self._session.execute(stmt)
        return [exception_to_domain(orm) for orm in result.scalars().all()]

    async def list_for_specialist(self, specialist_id: int) -> list[RecurringException]:
        # Join to the owning series so settle/expand can batch-load every
        # exception for one specialist in a single query.
        stmt = (
            select(RecurringExceptionORM)
            .join(
                RecurringAppointmentORM,
                RecurringExceptionORM.series_id == RecurringAppointmentORM.id,
            )
            .where(RecurringAppointmentORM.specialist_id == specialist_id)
        )
        result = await self._session.execute(stmt)
        return [exception_to_domain(orm) for orm in result.scalars().all()]
