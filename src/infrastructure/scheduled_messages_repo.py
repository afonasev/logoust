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

from src.domain.audit import AuditEvent
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessageStatus,
)
from src.infrastructure.db import Base


def _as_utc(value: datetime) -> datetime:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it (mirrors
    # reminders_repo._as_utc) — else due-time comparison shifts by the host offset.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_utc_opt(value: datetime | None) -> datetime | None:
    return None if value is None else _as_utc(value)


class ScheduledClientMessageORM(Base):
    __tablename__ = "scheduled_client_messages"
    __table_args__ = (
        # Delivery pass: queued rows whose due_at has arrived, ascending.
        Index("ix_scheduled_status_due", "status", "due_at"),
        # Client card: this client's queued rows.
        Index(
            "ix_scheduled_specialist_client_status",
            "specialist_id",
            "client_id",
            "status",
        ),
        # Supersede lookup: a queued row for the same target.
        Index(
            "ix_scheduled_specialist_target_status",
            "specialist_id",
            "target_key",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ScheduledClientMessageORM id={self.id} client={self.client_id} "
            f"due_at={self.due_at} status={self.status}>"
        )


def to_domain(orm: ScheduledClientMessageORM) -> ScheduledClientMessage:
    return ScheduledClientMessage(
        id=orm.id,
        specialist_id=orm.specialist_id,
        client_id=orm.client_id,
        chat_id=orm.chat_id,
        text=orm.text,
        target_key=orm.target_key,
        event=AuditEvent(orm.event),
        due_at=_as_utc(orm.due_at),
        status=ScheduledMessageStatus(orm.status),
        created_at=_as_utc(orm.created_at),
        sent_at=_as_utc_opt(orm.sent_at),
    )


class SqlAlchemyScheduledMessagesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue_superseding(
        self, message: ScheduledClientMessage
    ) -> tuple[ScheduledClientMessage, datetime | None]:
        # One transaction: cancel any queued row for the same (specialist, target),
        # then insert the new one. Returns the inserted row and the superseded due_at
        # (None when nothing was replaced) for the "previous send replaced" notice.
        existing = await self._session.execute(
            select(ScheduledClientMessageORM).where(
                ScheduledClientMessageORM.specialist_id == message.specialist_id,
                ScheduledClientMessageORM.target_key == message.target_key,
                ScheduledClientMessageORM.status == ScheduledMessageStatus.QUEUED.value,
            )
        )
        superseded_due_at: datetime | None = None
        for orm in existing.scalars().all():
            superseded_due_at = _as_utc(orm.due_at)
            orm.status = ScheduledMessageStatus.CANCELLED.value
        new_orm = ScheduledClientMessageORM(
            specialist_id=message.specialist_id,
            client_id=message.client_id,
            chat_id=message.chat_id,
            text=message.text,
            target_key=message.target_key,
            event=message.event.value,
            due_at=message.due_at,
            status=message.status.value,
            created_at=message.created_at,
            sent_at=message.sent_at,
        )
        self._session.add(new_orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(new_orm), superseded_due_at

    async def list_due(self, now: datetime) -> list[ScheduledClientMessage]:
        stmt = (
            select(ScheduledClientMessageORM)
            .where(
                ScheduledClientMessageORM.status == ScheduledMessageStatus.QUEUED.value,
                ScheduledClientMessageORM.due_at <= now,
            )
            .order_by(ScheduledClientMessageORM.due_at)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def mark_sent(self, message_id: int, at: datetime) -> None:
        await self._set_terminal(message_id, ScheduledMessageStatus.SENT, at)

    async def mark_failed(self, message_id: int, at: datetime) -> None:
        await self._set_terminal(message_id, ScheduledMessageStatus.FAILED, at)

    async def _set_terminal(
        self, message_id: int, status: ScheduledMessageStatus, at: datetime
    ) -> None:
        orm = await self._session.get(ScheduledClientMessageORM, message_id)
        if orm is None:  # pragma: no cover - pass only marks rows it just loaded
            return
        orm.status = status.value
        orm.sent_at = at
        await self._session.commit()

    async def cancel(self, message_id: int, specialist_id: int) -> bool:
        orm = await self._session.get(ScheduledClientMessageORM, message_id)
        # Owner-scoped: another specialist's row (or a non-queued one) is a no-op.
        if (
            orm is None
            or orm.specialist_id != specialist_id
            or orm.status != ScheduledMessageStatus.QUEUED.value
        ):
            return False
        orm.status = ScheduledMessageStatus.CANCELLED.value
        await self._session.commit()
        return True

    async def list_queued_for_client(
        self, specialist_id: int, client_id: int
    ) -> list[ScheduledClientMessage]:
        stmt = (
            select(ScheduledClientMessageORM)
            .where(
                ScheduledClientMessageORM.specialist_id == specialist_id,
                ScheduledClientMessageORM.client_id == client_id,
                ScheduledClientMessageORM.status == ScheduledMessageStatus.QUEUED.value,
            )
            .order_by(ScheduledClientMessageORM.due_at)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]
