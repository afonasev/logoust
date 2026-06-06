from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.audit import AuditEntry, AuditEvent, AuditKind, DeliveryStatus
from src.infrastructure.db import Base


class AuditLogORM(Base):
    __tablename__ = "audit_log"
    # Лента всегда читается по владельцу и времени (новейшее сверху); составной
    # индекс обслуживает и фильтр, и сортировку.
    __table_args__ = (
        Index("ix_audit_specialist_created", "specialist_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    client_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=True
    )
    # text/status/error несут только message-строки; для action они NULL.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditLogORM id={self.id} kind={self.kind} "
            f"event={self.event} specialist={self.specialist_id}>"
        )


def to_domain(orm: AuditLogORM) -> AuditEntry:
    return AuditEntry(
        id=orm.id,
        specialist_id=orm.specialist_id,
        created_at=orm.created_at,
        kind=AuditKind(orm.kind),
        event=AuditEvent(orm.event),
        client_id=orm.client_id,
        text=orm.text,
        status=DeliveryStatus(orm.status) if orm.status is not None else None,
        error=orm.error,
    )


class SqlAlchemyAuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(  # noqa: PLR0913
        self,
        *,
        specialist_id: int,
        kind: AuditKind,
        event: AuditEvent,
        client_id: int | None = None,
        text: str | None = None,
        status: DeliveryStatus | None = None,
        error: str | None = None,
    ) -> AuditEntry:
        orm = AuditLogORM(
            specialist_id=specialist_id,
            kind=kind.value,
            event=event.value,
            client_id=client_id,
            text=text,
            status=status.value if status is not None else None,
            error=error,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def list_for_specialist(
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[AuditEntry]:
        stmt = (
            select(AuditLogORM)
            .where(AuditLogORM.specialist_id == specialist_id)
            # id — вторичный ключ: разводит строки при равном created_at.
            .order_by(AuditLogORM.created_at.desc(), AuditLogORM.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def count_for_specialist(self, specialist_id: int) -> int:
        stmt = select(func.count()).where(AuditLogORM.specialist_id == specialist_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()
