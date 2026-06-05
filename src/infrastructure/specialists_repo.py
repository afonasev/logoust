from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.specialist import (
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DEFAULT_SLOT_MINUTES,
    DEFAULT_TIMEZONE,
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
