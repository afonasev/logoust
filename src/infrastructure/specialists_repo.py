from collections.abc import Mapping
from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.specialist import (
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DEFAULT_MORNING_NOTIFY_ENABLED,
    DEFAULT_MORNING_NOTIFY_TIME,
    DEFAULT_REMINDER_ENABLED,
    DEFAULT_REMINDER_TIME,
    DEFAULT_SLOT_MINUTES,
    DEFAULT_SUBSCRIPTION_PRESETS,
    DEFAULT_TIMEZONE,
    DEFAULT_WORKING_DAYS,
    ChatIdConflictError,
    Specialist,
)
from src.infrastructure.db import Base


class SpecialistORM(Base):
    __tablename__ = "specialists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invite_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    welcomed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_TIMEZONE
    )
    day_start: Mapped[str] = mapped_column(
        String(5), nullable=False, default=DEFAULT_DAY_START
    )
    day_end: Mapped[str] = mapped_column(
        String(5), nullable=False, default=DEFAULT_DAY_END
    )
    slot_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_SLOT_MINUTES
    )
    working_days: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DEFAULT_WORKING_DAYS
    )
    reminder_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=DEFAULT_REMINDER_ENABLED
    )
    reminder_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default=DEFAULT_REMINDER_TIME
    )
    reminder_last_run_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    morning_notify_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=DEFAULT_MORNING_NOTIFY_ENABLED
    )
    morning_notify_time: Mapped[str] = mapped_column(
        String(5), nullable=False, default=DEFAULT_MORNING_NOTIFY_TIME
    )
    morning_notify_last_run_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    subscription_presets: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_SUBSCRIPTION_PRESETS
    )

    def __repr__(self) -> str:
        return f"<SpecialistORM id={self.id} token={self.invite_token[:6]}…>"


def to_domain(orm: SpecialistORM) -> Specialist:
    return Specialist(
        id=orm.id,
        invite_token=orm.invite_token,
        telegram_chat_id=orm.telegram_chat_id,
        telegram_username=orm.telegram_username,
        welcomed_at=orm.welcomed_at,
        created_at=orm.created_at,
        timezone=orm.timezone,
        day_start=orm.day_start,
        day_end=orm.day_end,
        slot_minutes=orm.slot_minutes,
        working_days=orm.working_days,
        reminder_enabled=bool(orm.reminder_enabled),
        reminder_time=orm.reminder_time,
        reminder_last_run_on=orm.reminder_last_run_on,
        morning_notify_enabled=bool(orm.morning_notify_enabled),
        morning_notify_time=orm.morning_notify_time,
        morning_notify_last_run_on=orm.morning_notify_last_run_on,
        subscription_presets=orm.subscription_presets,
    )


class SqlAlchemySpecialistsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, specialist: Specialist) -> Specialist:
        orm = SpecialistORM(
            invite_token=specialist.invite_token,
            telegram_chat_id=specialist.telegram_chat_id,
            telegram_username=specialist.telegram_username,
            welcomed_at=specialist.welcomed_at,
            created_at=specialist.created_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def find_by_token(self, token: str) -> Specialist | None:
        stmt = select(SpecialistORM).where(SpecialistORM.invite_token == token)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return to_domain(orm)

    async def find_by_chat_id(self, chat_id: int) -> Specialist | None:
        stmt = select(SpecialistORM).where(SpecialistORM.telegram_chat_id == chat_id)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        if orm is None:
            return None
        return to_domain(orm)

    async def get(self, specialist_id: int) -> Specialist | None:
        orm = await self._session.get(SpecialistORM, specialist_id)
        return to_domain(orm) if orm is not None else None

    async def update_settings(
        self, specialist_id: int, fields: Mapping[str, object]
    ) -> Specialist | None:
        orm = await self._session.get(SpecialistORM, specialist_id)
        if orm is None:
            return None
        for key, value in fields.items():
            setattr(orm, key, value)
        await self._session.commit()
        return to_domain(orm)

    async def list_reminder_candidates(self) -> list[Specialist]:
        stmt = select(SpecialistORM).where(SpecialistORM.reminder_enabled.is_(True))
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def mark_reminder_run(self, specialist_id: int, run_on: date) -> None:
        orm = await self._session.get(SpecialistORM, specialist_id)
        if orm is None:  # pragma: no cover - pass only marks loaded candidates
            return
        orm.reminder_last_run_on = run_on
        await self._session.commit()

    async def list_digest_candidates(self) -> list[Specialist]:
        # Only welcomed specialists can receive a digest, so skip those without a
        # chat to send to — the digest pass then never has to guard a None chat_id.
        stmt = select(SpecialistORM).where(
            SpecialistORM.morning_notify_enabled.is_(True),
            SpecialistORM.telegram_chat_id.is_not(None),
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def mark_digest_run(self, specialist_id: int, run_on: date) -> None:
        orm = await self._session.get(SpecialistORM, specialist_id)
        if orm is None:  # pragma: no cover - pass only marks loaded candidates
            return
        orm.morning_notify_last_run_on = run_on
        await self._session.commit()

    async def mark_welcomed(
        self,
        specialist_id: int,
        *,
        telegram_chat_id: int,
        telegram_username: str | None,
        welcomed_at: datetime,
    ) -> None:
        orm = await self._session.get(SpecialistORM, specialist_id)
        if orm is None:
            msg = f"Specialist with id {specialist_id} not found"
            raise ValueError(msg)
        if orm.welcomed_at is not None:
            return
        orm.telegram_chat_id = telegram_chat_id
        orm.telegram_username = telegram_username
        orm.welcomed_at = welcomed_at
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            msg = f"chat_id={telegram_chat_id} already bound to another specialist"
            raise ChatIdConflictError(msg) from exc
