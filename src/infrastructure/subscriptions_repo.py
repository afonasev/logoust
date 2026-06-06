from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.db import Base


class SubscriptionORM(Base):
    __tablename__ = "subscriptions"
    # Активный абонемент клиента ищется по (client_id, status); индекс обслуживает
    # и проверку инварианта «один активный», и кнопку на карточке клиента.
    __table_args__ = (Index("ix_subscriptions_client_status", "client_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clients.id"), nullable=False
    )
    # Денормализованный владелец — дешёвая проверка прав без джойна (design.md, реш. 2).
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    purchased: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SubscriptionORM id={self.id} client={self.client_id} "
            f"remaining={self.remaining} status={self.status}>"
        )


def to_domain(orm: SubscriptionORM) -> Subscription:
    return Subscription(
        id=orm.id,
        client_id=orm.client_id,
        specialist_id=orm.specialist_id,
        purchased=orm.purchased,
        remaining=orm.remaining,
        status=SubscriptionStatus(orm.status),
        created_at=orm.created_at,
        closed_at=orm.closed_at,
    )


class SqlAlchemySubscriptionsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, subscription: Subscription) -> Subscription:
        orm = SubscriptionORM(
            client_id=subscription.client_id,
            specialist_id=subscription.specialist_id,
            purchased=subscription.purchased,
            remaining=subscription.remaining,
            status=subscription.status.value,
            created_at=subscription.created_at,
            closed_at=subscription.closed_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return to_domain(orm)

    async def get_active(
        self, client_id: int, specialist_id: int
    ) -> Subscription | None:
        stmt = select(SubscriptionORM).where(
            SubscriptionORM.client_id == client_id,
            SubscriptionORM.specialist_id == specialist_id,
            SubscriptionORM.status == SubscriptionStatus.ACTIVE.value,
        )
        result = await self._session.execute(stmt)
        orm = result.scalar_one_or_none()
        return to_domain(orm) if orm is not None else None

    async def get_for_specialist(
        self, subscription_id: int, specialist_id: int
    ) -> Subscription | None:
        orm = await self._get_owned(subscription_id, specialist_id)
        return to_domain(orm) if orm is not None else None

    async def list_active_for_specialist(
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Subscription]:
        stmt = (
            select(SubscriptionORM)
            .where(
                SubscriptionORM.specialist_id == specialist_id,
                SubscriptionORM.status == SubscriptionStatus.ACTIVE.value,
            )
            .order_by(SubscriptionORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def list_closed_for_specialist(
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Subscription]:
        stmt = (
            select(SubscriptionORM)
            .where(
                SubscriptionORM.specialist_id == specialist_id,
                SubscriptionORM.status == SubscriptionStatus.CLOSED.value,
            )
            .order_by(SubscriptionORM.closed_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return [to_domain(orm) for orm in result.scalars().all()]

    async def update_counters(
        self,
        subscription_id: int,
        specialist_id: int,
        *,
        purchased: int,
        remaining: int,
    ) -> Subscription | None:
        orm = await self._get_owned(subscription_id, specialist_id)
        if orm is None:
            return None
        orm.purchased = purchased
        orm.remaining = remaining
        await self._session.commit()
        return to_domain(orm)

    async def close(
        self, subscription_id: int, specialist_id: int, *, closed_at: datetime
    ) -> Subscription | None:
        orm = await self._get_owned(subscription_id, specialist_id)
        if orm is None:
            return None
        orm.status = SubscriptionStatus.CLOSED.value
        orm.closed_at = closed_at
        await self._session.commit()
        return to_domain(orm)

    async def _get_owned(
        self, subscription_id: int, specialist_id: int
    ) -> SubscriptionORM | None:
        stmt = select(SubscriptionORM).where(
            SubscriptionORM.id == subscription_id,
            SubscriptionORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
