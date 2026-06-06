from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.deduction import DeductionOutcome
from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.subscriptions_repo import (
    SqlAlchemySubscriptionDeductionsRepo,
    SqlAlchemySubscriptionsRepo,
    SubscriptionDeductionORM,
)

_SP = 1
_CLIENT = 10
_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
_APPT_AT = datetime(2026, 6, 7, 9, 0, tzinfo=UTC)


def _sub(*, remaining: int = 5, status: SubscriptionStatus = SubscriptionStatus.ACTIVE):
    return Subscription(
        id=None,
        client_id=_CLIENT,
        specialist_id=_SP,
        purchased=8,
        remaining=remaining,
        status=status,
        created_at=_NOW,
    )


async def _add_sub(session: AsyncSession, **kwargs) -> int:
    saved = await SqlAlchemySubscriptionsRepo(session).add(_sub(**kwargs))
    assert saved.id is not None
    return saved.id


async def _remaining(session: AsyncSession, sub_id: int) -> int:
    sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
    assert sub is not None
    return sub.remaining


async def test_add_auto_deducts_and_snapshots(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=5)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    result = await repo.add_auto(
        subscription_id=sub_id,
        appointment_id=100,
        appointment_starts_at=_APPT_AT,
        appointment_comment="логопед",
        created_at=_NOW,
    )
    assert result.outcome is DeductionOutcome.DEDUCTED
    assert result.remaining == 4
    assert result.deduction is not None
    assert result.deduction.appointment_id == 100
    assert result.deduction.appointment_starts_at == _APPT_AT
    assert result.deduction.appointment_comment == "логопед"
    assert await _remaining(session, sub_id) == 4
    journal = await repo.list_active_for_subscription(sub_id)
    assert len(journal) == 1


async def test_add_auto_duplicate_is_idempotent(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=5)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    first = await repo.add_auto(
        subscription_id=sub_id,
        appointment_id=100,
        appointment_starts_at=_APPT_AT,
        appointment_comment=None,
        created_at=_NOW,
    )
    assert first.outcome is DeductionOutcome.DEDUCTED
    second = await repo.add_auto(
        subscription_id=sub_id,
        appointment_id=100,
        appointment_starts_at=_APPT_AT,
        appointment_comment=None,
        created_at=_NOW,
    )
    assert second.outcome is DeductionOutcome.DUPLICATE
    # Only one charge: remaining dropped once, one journal row.
    assert await _remaining(session, sub_id) == 4
    assert len(await repo.list_active_for_subscription(sub_id)) == 1


async def test_add_auto_exhausted_writes_no_row(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=0)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    result = await repo.add_auto(
        subscription_id=sub_id,
        appointment_id=100,
        appointment_starts_at=_APPT_AT,
        appointment_comment=None,
        created_at=_NOW,
    )
    assert result.outcome is DeductionOutcome.EXHAUSTED
    assert await _remaining(session, sub_id) == 0
    assert await repo.list_active_for_subscription(sub_id) == []


async def test_add_manual_deducts_without_appointment(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.appointment_id is None
    assert await _remaining(session, sub_id) == 2


async def test_add_manual_at_zero_returns_none(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=0)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    assert (
        await repo.add_manual(
            subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
        )
        is None
    )
    assert await repo.list_active_for_subscription(sub_id) == []


async def test_add_manual_wrong_specialist_returns_none(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    assert (
        await repo.add_manual(subscription_id=sub_id, specialist_id=99, created_at=_NOW)
        is None
    )
    assert await _remaining(session, sub_id) == 3


async def test_get_for_specialist_isolation(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.id is not None
    assert await repo.get_for_specialist(deduction.id, _SP) is not None
    assert await repo.get_for_specialist(deduction.id, 99) is None


async def test_set_closing_comment_on_active(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.id is not None
    updated = await repo.set_closing_comment(deduction.id, _SP, comment="хорошо")
    assert updated is not None
    assert updated.closing_comment == "хорошо"
    assert await repo.set_closing_comment(404, _SP, comment="x") is None


async def test_set_closing_comment_blocked_on_closed(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    subs = SqlAlchemySubscriptionsRepo(session)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.id is not None
    await subs.close(sub_id, _SP, closed_at=_NOW)
    assert await repo.set_closing_comment(deduction.id, _SP, comment="x") is None


async def test_cancel_returns_remaining_and_hides_row(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.id is not None
    # remaining is 2 after the manual deduction.
    subscription = await repo.cancel(deduction.id, _SP, cancelled_at=_NOW)
    assert subscription is not None
    assert subscription.remaining == 3
    # Hidden from the journal, but the row stays (holds the idempotency lock).
    assert await repo.list_active_for_subscription(sub_id) == []
    # Idempotent: a second cancel does not bump remaining again.
    assert await repo.cancel(deduction.id, _SP, cancelled_at=_NOW) is None
    assert await _remaining(session, sub_id) == 3


async def test_cancel_blocked_on_closed_or_foreign(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    subs = SqlAlchemySubscriptionsRepo(session)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    deduction = await repo.add_manual(
        subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
    )
    assert deduction is not None
    assert deduction.id is not None
    assert await repo.cancel(deduction.id, 99, cancelled_at=_NOW) is None
    await subs.close(sub_id, _SP, closed_at=_NOW)
    assert await repo.cancel(deduction.id, _SP, cancelled_at=_NOW) is None


async def test_exists_for_appointment(session: AsyncSession):
    sub_id = await _add_sub(session, remaining=3)
    repo = SqlAlchemySubscriptionDeductionsRepo(session)
    assert await repo.exists_for_appointment(100) is False
    await repo.add_auto(
        subscription_id=sub_id,
        appointment_id=100,
        appointment_starts_at=_APPT_AT,
        appointment_comment=None,
        created_at=_NOW,
    )
    assert await repo.exists_for_appointment(100) is True


def test_orm_repr_includes_ids():
    orm = SubscriptionDeductionORM(id=7, subscription_id=3, appointment_id=100)
    assert "subscription=3" in repr(orm)
    assert "appointment=100" in repr(orm)
