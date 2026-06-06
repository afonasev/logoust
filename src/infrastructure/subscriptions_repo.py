from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.deduction import (
    DeductionOutcome,
    DeductionResult,
    SubscriptionDeduction,
)
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
    # Anti-duplicate напоминания (payment reminder); см. design.md, решение 3.
    payment_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
        payment_reminded_at=orm.payment_reminded_at,
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
            payment_reminded_at=subscription.payment_reminded_at,
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

    async def mark_payment_reminded(
        self, subscription_id: int, at: datetime | None
    ) -> None:
        orm = await self._session.get(SubscriptionORM, subscription_id)
        if orm is None:  # pragma: no cover - callers hold a freshly fetched row
            return
        orm.payment_reminded_at = at
        await self._session.commit()

    async def _get_owned(
        self, subscription_id: int, specialist_id: int
    ) -> SubscriptionORM | None:
        stmt = select(SubscriptionORM).where(
            SubscriptionORM.id == subscription_id,
            SubscriptionORM.specialist_id == specialist_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


def _as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on read; we store aware UTC, so re-attach it (mirrors
    # appointments_repo._as_utc) — else wall-clock conversion would shift the value.
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SubscriptionDeductionORM(Base):
    __tablename__ = "subscription_deductions"
    __table_args__ = (
        # Idempotency lock: at most one deduction per appointment. Partial so manual
        # deductions (appointment_id IS NULL) never collide with each other
        # (design.md, решение 1). A cancelled row keeps the lock (решение 4).
        Index(
            "uq_subscription_deductions_appointment",
            "appointment_id",
            unique=True,
            sqlite_where=text("appointment_id IS NOT NULL"),
        ),
        Index("ix_subscription_deductions_subscription", "subscription_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id"), nullable=False
    )
    # ON DELETE SET NULL keeps the journal row when its appointment is hard-deleted —
    # the snapshots below preserve the facts (design.md, решение 5).
    appointment_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    appointment_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    appointment_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    closing_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SubscriptionDeductionORM id={self.id} "
            f"subscription={self.subscription_id} appointment={self.appointment_id}>"
        )


def deduction_to_domain(orm: SubscriptionDeductionORM) -> SubscriptionDeduction:
    return SubscriptionDeduction(
        id=orm.id,
        subscription_id=orm.subscription_id,
        appointment_id=orm.appointment_id,
        appointment_starts_at=_as_utc(orm.appointment_starts_at),
        appointment_comment=orm.appointment_comment,
        closing_comment=orm.closing_comment,
        created_at=_as_utc(orm.created_at),  # type: ignore[arg-type] — never None
        cancelled_at=_as_utc(orm.cancelled_at),
    )


class SqlAlchemySubscriptionDeductionsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_auto(
        self,
        *,
        subscription_id: int,
        appointment_id: int,
        appointment_starts_at: datetime,
        appointment_comment: str | None,
        created_at: datetime,
    ) -> DeductionResult:
        """Lock-insert then conditional decrement, one transaction (design.md, реш. 1).

        The insert hits the unique index first: a second pass over the same
        appointment raises IntegrityError → DUPLICATE. The decrement is conditional
        (`WHERE remaining > 0`) so two same-day meetings never lose an update; zero
        rows updated → the subscription is exhausted → EXHAUSTED (insert rolled back).
        """
        orm = SubscriptionDeductionORM(
            subscription_id=subscription_id,
            appointment_id=appointment_id,
            appointment_starts_at=appointment_starts_at,
            appointment_comment=appointment_comment,
            created_at=created_at,
        )
        self._session.add(orm)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return DeductionResult(outcome=DeductionOutcome.DUPLICATE)
        if not await self._decrement(subscription_id):
            await self._session.rollback()  # also undoes the lock insert
            return DeductionResult(outcome=DeductionOutcome.EXHAUSTED)
        await self._session.commit()
        remaining = await self._remaining(subscription_id)
        return DeductionResult(
            outcome=DeductionOutcome.DEDUCTED,
            deduction=deduction_to_domain(orm),
            remaining=remaining,
        )

    async def add_manual(
        self, *, subscription_id: int, specialist_id: int, created_at: datetime
    ) -> SubscriptionDeduction | None:
        """Atomic manual deduction: conditional decrement + journal row (no lock).

        Returns None when the subscription is not the specialist's active one or
        `remaining` is already 0 — in which case no row is written (spec
        `subscriptions`: ручное списание при нулевом остатке).
        """
        if not await self._decrement(subscription_id, specialist_id=specialist_id):
            await self._session.rollback()
            return None
        orm = SubscriptionDeductionORM(
            subscription_id=subscription_id,
            appointment_id=None,
            created_at=created_at,
        )
        self._session.add(orm)
        await self._session.flush()
        await self._session.commit()
        return deduction_to_domain(orm)

    async def list_active_for_subscription(
        self, subscription_id: int
    ) -> list[SubscriptionDeduction]:
        stmt = (
            select(SubscriptionDeductionORM)
            .where(
                SubscriptionDeductionORM.subscription_id == subscription_id,
                SubscriptionDeductionORM.cancelled_at.is_(None),
            )
            .order_by(SubscriptionDeductionORM.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [deduction_to_domain(orm) for orm in result.scalars().all()]

    async def get_for_specialist(
        self, deduction_id: int, specialist_id: int
    ) -> SubscriptionDeduction | None:
        orm = await self._get_owned(deduction_id, specialist_id)
        return deduction_to_domain(orm) if orm is not None else None

    async def set_closing_comment(
        self, deduction_id: int, specialist_id: int, *, comment: str | None
    ) -> SubscriptionDeduction | None:
        orm = await self._get_owned(deduction_id, specialist_id)
        if orm is None:
            return None
        sub = await self._session.get(SubscriptionORM, orm.subscription_id)
        # Only editable while the subscription is active (closed → read-only journal).
        if sub is None or sub.status != SubscriptionStatus.ACTIVE.value:
            return None
        orm.closing_comment = comment
        await self._session.commit()
        return deduction_to_domain(orm)

    async def cancel(
        self, deduction_id: int, specialist_id: int, *, cancelled_at: datetime
    ) -> Subscription | None:
        """Soft-cancel: mark cancelled + return remaining +1, one transaction.

        Idempotent (an already-cancelled row returns None without touching the
        counter) and only on an active subscription. The row stays to hold the
        idempotency lock so the cancelled meeting is not re-deducted (design.md,
        решение 4).
        """
        orm = await self._get_owned(deduction_id, specialist_id)
        if orm is None or orm.cancelled_at is not None:
            return None
        sub = await self._session.get(SubscriptionORM, orm.subscription_id)
        if sub is None or sub.status != SubscriptionStatus.ACTIVE.value:
            return None
        orm.cancelled_at = cancelled_at
        sub.remaining += 1
        await self._session.commit()
        return to_domain(sub)

    async def exists_for_appointment(self, appointment_id: int) -> bool:
        stmt = select(SubscriptionDeductionORM.id).where(
            SubscriptionDeductionORM.appointment_id == appointment_id
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def _decrement(
        self, subscription_id: int, *, specialist_id: int | None = None
    ) -> bool:
        """Conditional `remaining -= 1 WHERE remaining > 0`; True if a row changed.

        Atomic by construction (no read-modify-write), so concurrent decrements of
        the same active subscription cannot lose an update (design.md, решение 1/4).
        """
        conditions = [
            SubscriptionORM.id == subscription_id,
            SubscriptionORM.remaining > 0,
        ]
        if specialist_id is not None:
            conditions.extend(
                (
                    SubscriptionORM.specialist_id == specialist_id,
                    SubscriptionORM.status == SubscriptionStatus.ACTIVE.value,
                )
            )
        stmt = (
            update(SubscriptionORM)
            .where(*conditions)
            .values(remaining=SubscriptionORM.remaining - 1)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def _remaining(self, subscription_id: int) -> int | None:
        stmt = select(SubscriptionORM.remaining).where(
            SubscriptionORM.id == subscription_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_owned(
        self, deduction_id: int, specialist_id: int
    ) -> SubscriptionDeductionORM | None:
        # Ownership is via the parent subscription's denormalised specialist_id.
        stmt = (
            select(SubscriptionDeductionORM)
            .join(
                SubscriptionORM,
                SubscriptionORM.id == SubscriptionDeductionORM.subscription_id,
            )
            .where(
                SubscriptionDeductionORM.id == deduction_id,
                SubscriptionORM.specialist_id == specialist_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
