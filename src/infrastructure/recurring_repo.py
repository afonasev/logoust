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

from src.domain.recurring import (
    RecurringSchedule,
    RecurringSlot,
    RecurringSlotOverride,
)
from src.infrastructure.db import Base


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it (mirrors
    # appointments_repo._as_utc). None stays None — moved_to may be absent.
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RecurringScheduleORM(Base):
    __tablename__ = "recurring_schedules"
    # Read-merge and settle always filter by owner and activeness.
    __table_args__ = (
        Index("ix_recurring_schedules_specialist_active", "specialist_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<RecurringScheduleORM id={self.id} client={self.client_id} "
            f"active={self.active}>"
        )


class RecurringSlotORM(Base):
    __tablename__ = "recurring_slots"
    __table_args__ = (
        Index("ix_recurring_slots_schedule_active", "schedule_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recurring_schedules.id"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    time_hhmm: Mapped[str] = mapped_column(String(5), nullable=False)
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
            f"<RecurringSlotORM id={self.id} schedule={self.schedule_id} "
            f"weekday={self.weekday} time={self.time_hhmm} active={self.active}>"
        )


class RecurringSlotOverrideORM(Base):
    __tablename__ = "recurring_slot_overrides"
    __table_args__ = (
        UniqueConstraint("slot_id", "original_date", name="uq_override_slot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recurring_slots.id"), nullable=False
    )
    original_date: Mapped[date] = mapped_column(Date, nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False)
    moved_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<RecurringSlotOverrideORM id={self.id} slot={self.slot_id} "
            f"date={self.original_date} skipped={self.skipped} "
            f"moved={self.moved_to is not None}>"
        )


def schedule_to_domain(orm: RecurringScheduleORM) -> RecurringSchedule:
    return RecurringSchedule(
        id=orm.id,
        specialist_id=orm.specialist_id,
        client_id=orm.client_id,
        comment=orm.comment,
        active=bool(orm.active),
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type]
        updated_at=_as_utc(orm.updated_at),  # type: ignore[arg-type]
    )


def slot_to_domain(orm: RecurringSlotORM) -> RecurringSlot:
    return RecurringSlot(
        id=orm.id,
        schedule_id=orm.schedule_id,
        weekday=orm.weekday,
        time_hhmm=orm.time_hhmm,
        active=bool(orm.active),
        start_date=orm.start_date,
        materialized_through=orm.materialized_through,
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type]
        updated_at=_as_utc(orm.updated_at),  # type: ignore[arg-type]
    )


def override_to_domain(orm: RecurringSlotOverrideORM) -> RecurringSlotOverride:
    return RecurringSlotOverride(
        id=orm.id,
        slot_id=orm.slot_id,
        original_date=orm.original_date,
        skipped=bool(orm.skipped),
        moved_to=_as_utc(orm.moved_to),
        comment=orm.comment,
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type]
    )


class SqlAlchemyRecurringScheduleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, schedule: RecurringSchedule) -> RecurringSchedule:
        orm = RecurringScheduleORM(
            specialist_id=schedule.specialist_id,
            client_id=schedule.client_id,
            comment=schedule.comment,
            active=schedule.active,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return schedule_to_domain(orm)

    async def list_active_for_specialist(
        self, specialist_id: int
    ) -> list[RecurringSchedule]:
        stmt = (
            select(RecurringScheduleORM)
            .where(
                RecurringScheduleORM.specialist_id == specialist_id,
                RecurringScheduleORM.active.is_(True),
            )
            .order_by(RecurringScheduleORM.id.asc())
        )
        result = await self._session.execute(stmt)
        return [schedule_to_domain(orm) for orm in result.scalars().all()]

    async def get_for_specialist(
        self, schedule_id: int, specialist_id: int
    ) -> RecurringSchedule | None:
        orm = await self._get_owned(schedule_id, specialist_id)
        return schedule_to_domain(orm) if orm is not None else None

    async def set_active(
        self,
        schedule_id: int,
        specialist_id: int,
        *,
        active: bool,
        updated_at: datetime,
    ) -> RecurringSchedule | None:
        orm = await self._get_owned(schedule_id, specialist_id)
        if orm is None:
            return None
        orm.active = active
        orm.updated_at = updated_at
        await self._session.commit()
        return schedule_to_domain(orm)

    async def set_comment(
        self,
        schedule_id: int,
        specialist_id: int,
        *,
        comment: str | None,
        updated_at: datetime,
    ) -> RecurringSchedule | None:
        orm = await self._get_owned(schedule_id, specialist_id)
        if orm is None:
            return None
        orm.comment = comment
        orm.updated_at = updated_at
        await self._session.commit()
        return schedule_to_domain(orm)

    async def _get_owned(
        self, schedule_id: int, specialist_id: int
    ) -> RecurringScheduleORM | None:
        stmt = select(RecurringScheduleORM).where(
            RecurringScheduleORM.id == schedule_id,
            RecurringScheduleORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SqlAlchemyRecurringSlotRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, slot: RecurringSlot) -> RecurringSlot:
        orm = RecurringSlotORM(
            schedule_id=slot.schedule_id,
            weekday=slot.weekday,
            time_hhmm=slot.time_hhmm,
            active=slot.active,
            start_date=slot.start_date,
            materialized_through=slot.materialized_through,
            created_at=slot.created_at,
            updated_at=slot.updated_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return slot_to_domain(orm)

    async def list_for_schedule(self, schedule_id: int) -> list[RecurringSlot]:
        # Active slots only: removed slots keep their frozen history but never show
        # in the rule, occurrences, or the configure list.
        stmt = (
            select(RecurringSlotORM)
            .where(
                RecurringSlotORM.schedule_id == schedule_id,
                RecurringSlotORM.active.is_(True),
            )
            .order_by(RecurringSlotORM.id.asc())
        )
        result = await self._session.execute(stmt)
        return [slot_to_domain(orm) for orm in result.scalars().all()]

    async def get_for_specialist(
        self, slot_id: int, specialist_id: int
    ) -> RecurringSlot | None:
        orm = await self._get_owned(slot_id, specialist_id)
        return slot_to_domain(orm) if orm is not None else None

    async def set_active(
        self, slot_id: int, specialist_id: int, *, active: bool, updated_at: datetime
    ) -> RecurringSlot | None:
        orm = await self._get_owned(slot_id, specialist_id)
        if orm is None:
            return None
        orm.active = active
        orm.updated_at = updated_at
        await self._session.commit()
        return slot_to_domain(orm)

    async def set_materialized_through(
        self, slot_id: int, *, materialized_through: date
    ) -> None:
        orm = await self._session.get(RecurringSlotORM, slot_id)
        if orm is None:  # pragma: no cover - settle only passes loaded slot ids
            return
        orm.materialized_through = materialized_through
        await self._session.commit()

    async def update_rule(  # noqa: PLR0913
        self,
        slot_id: int,
        specialist_id: int,
        *,
        weekday: int,
        time_hhmm: str,
        start_date: date,
        materialized_through: date,
        updated_at: datetime,
    ) -> RecurringSlot | None:
        orm = await self._get_owned(slot_id, specialist_id)
        if orm is None:
            return None
        orm.weekday = weekday
        orm.time_hhmm = time_hhmm
        orm.start_date = start_date
        orm.materialized_through = materialized_through
        orm.updated_at = updated_at
        await self._session.commit()
        return slot_to_domain(orm)

    async def _get_owned(
        self, slot_id: int, specialist_id: int
    ) -> RecurringSlotORM | None:
        # Ownership is via the slot's schedule: a slot carries no specialist_id.
        stmt = (
            select(RecurringSlotORM)
            .join(
                RecurringScheduleORM,
                RecurringSlotORM.schedule_id == RecurringScheduleORM.id,
            )
            .where(
                RecurringSlotORM.id == slot_id,
                RecurringScheduleORM.specialist_id == specialist_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class SqlAlchemyRecurringSlotOverrideRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(  # noqa: PLR0913
        self,
        slot_id: int,
        original_date: date,
        *,
        skipped: bool,
        moved_to: datetime | None,
        comment: str | None,
        created_at: datetime,
    ) -> RecurringSlotOverride:
        # One row per (slot_id, original_date); the caller passes the full desired
        # triple (skip/move/comment), so re-acting on a date overwrites all axes.
        stmt = (
            sqlite_insert(RecurringSlotOverrideORM)
            .values(
                slot_id=slot_id,
                original_date=original_date,
                skipped=skipped,
                moved_to=moved_to,
                comment=comment,
                created_at=created_at,
            )
            .on_conflict_do_update(
                index_elements=["slot_id", "original_date"],
                set_={"skipped": skipped, "moved_to": moved_to, "comment": comment},
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()
        loaded = await self._session.execute(
            select(RecurringSlotOverrideORM).where(
                RecurringSlotOverrideORM.slot_id == slot_id,
                RecurringSlotOverrideORM.original_date == original_date,
            )
        )
        return override_to_domain(loaded.scalar_one())

    async def list_for_slot(self, slot_id: int) -> list[RecurringSlotOverride]:
        stmt = select(RecurringSlotOverrideORM).where(
            RecurringSlotOverrideORM.slot_id == slot_id
        )
        result = await self._session.execute(stmt)
        return [override_to_domain(orm) for orm in result.scalars().all()]

    async def list_for_specialist(
        self, specialist_id: int
    ) -> list[RecurringSlotOverride]:
        # Join through slots → schedules so settle/read can batch-load every
        # override for one specialist in a single query.
        stmt = (
            select(RecurringSlotOverrideORM)
            .join(
                RecurringSlotORM,
                RecurringSlotOverrideORM.slot_id == RecurringSlotORM.id,
            )
            .join(
                RecurringScheduleORM,
                RecurringSlotORM.schedule_id == RecurringScheduleORM.id,
            )
            .where(RecurringScheduleORM.specialist_id == specialist_id)
        )
        result = await self._session.execute(stmt)
        return [override_to_domain(orm) for orm in result.scalars().all()]
