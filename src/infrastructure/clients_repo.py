from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.client import Client, ClientStatus
from src.infrastructure.db import Base


class ClientORM(Base):
    __tablename__ = "clients"
    # Списки клиентов всегда фильтруются по владельцу и статусу; составной индекс
    # обслуживает и выборку «все мои» (по левому префиксу specialist_id).
    __table_args__ = (Index("ix_clients_specialist_status", "specialist_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    child_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contact_telegram: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_contacts: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Канал связи. invite_token уникален (поиск при онбординге); telegram_chat_id
    # не уникален намеренно — один аккаунт может быть привязан к нескольким
    # карточкам (тестирование специалистом под своим аккаунтом).
    invite_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<ClientORM id={self.id} child={self.child_name!r} status={self.status}>"
        )


def to_domain(orm: ClientORM) -> Client:
    return Client(
        id=orm.id,
        specialist_id=orm.specialist_id,
        child_name=orm.child_name,
        contact_name=orm.contact_name,
        contact_phone=orm.contact_phone,
        contact_telegram=orm.contact_telegram,
        extra_contacts=orm.extra_contacts,
        note=orm.note,
        status=ClientStatus(orm.status),
        archived_at=orm.archived_at,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        invite_token=orm.invite_token,
        telegram_chat_id=orm.telegram_chat_id,
        linked_at=orm.linked_at,
    )


class SqlAlchemyClientsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, client: Client) -> Client:
        orm = ClientORM(
            specialist_id=client.specialist_id,
            child_name=client.child_name,
            contact_name=client.contact_name,
            contact_phone=client.contact_phone,
            contact_telegram=client.contact_telegram,
            extra_contacts=client.extra_contacts,
            note=client.note,
            status=client.status.value,
            archived_at=client.archived_at,
            created_at=client.created_at,
            updated_at=client.updated_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def get_for_specialist(
        self, client_id: int, specialist_id: int
    ) -> Client | None:
        orm = await self._get_owned(client_id, specialist_id)
        return to_domain(orm) if orm is not None else None

    async def list_by_status(
        self, specialist_id: int, status: ClientStatus
    ) -> list[Client]:
        stmt = (
            select(ClientORM)
            .where(
                ClientORM.specialist_id == specialist_id,
                ClientORM.status == status.value,
            )
            .order_by(ClientORM.child_name)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_active(
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Client]:
        stmt = (
            select(ClientORM)
            .where(
                ClientORM.specialist_id == specialist_id,
                ClientORM.status == ClientStatus.ACTIVE.value,
            )
            .order_by(ClientORM.child_name)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_archived(
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Client]:
        stmt = (
            select(ClientORM)
            .where(
                ClientORM.specialist_id == specialist_id,
                ClientORM.status == ClientStatus.ARCHIVED.value,
            )
            .order_by(ClientORM.archived_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def update_fields(
        self,
        client_id: int,
        specialist_id: int,
        fields: Mapping[str, object],
        *,
        updated_at: datetime,
    ) -> Client | None:
        orm = await self._get_owned(client_id, specialist_id)
        if orm is None:
            return None
        for key, value in fields.items():
            setattr(orm, key, value)
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def set_status(
        self,
        client_id: int,
        specialist_id: int,
        status: ClientStatus,
        *,
        archived_at: datetime | None,
        updated_at: datetime,
    ) -> Client | None:
        orm = await self._get_owned(client_id, specialist_id)
        if orm is None:
            return None
        orm.status = status.value
        orm.archived_at = archived_at
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def set_invite_token(
        self,
        client_id: int,
        specialist_id: int,
        token: str,
        *,
        updated_at: datetime,
    ) -> Client | None:
        orm = await self._get_owned(client_id, specialist_id)
        if orm is None:
            return None
        orm.invite_token = token
        orm.updated_at = updated_at
        await self._session.commit()
        return to_domain(orm)

    async def find_by_invite_token(self, token: str) -> Client | None:
        stmt = select(ClientORM).where(ClientORM.invite_token == token)
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return to_domain(orm) if orm is not None else None

    async def link_telegram(
        self,
        client_id: int,
        *,
        telegram_chat_id: int,
        username: str | None,
        linked_at: datetime,
        updated_at: datetime,
    ) -> Client | None:
        # Idempotent rebind: всегда перезаписываем chat_id/linked_at (см. design.md).
        # Поиск по id без specialist_id — клиент переходит по ссылке сам, владелец
        # уже зафиксирован токеном.
        orm = await self._session.get(ClientORM, client_id)
        if orm is None:
            return None
        orm.telegram_chat_id = telegram_chat_id
        orm.linked_at = linked_at
        orm.updated_at = updated_at
        # Автозахват username из Telegram: заполняем поле, только если специалист
        # не вписал контакт вручную (ручной ввод не затираем).
        if username and not orm.contact_telegram:
            orm.contact_telegram = username
        await self._session.commit()
        return to_domain(orm)

    async def _get_owned(self, client_id: int, specialist_id: int) -> ClientORM | None:
        stmt = select(ClientORM).where(
            ClientORM.id == client_id,
            ClientORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
