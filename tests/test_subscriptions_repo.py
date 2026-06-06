from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.subscriptions_repo import (
    SqlAlchemySubscriptionsRepo,
    SubscriptionORM,
    to_domain,
)

_SP = 1
_CLIENT = 10


def _make(
    *, client_id: int = _CLIENT, specialist_id: int = _SP, meetings: int = 8
) -> Subscription:
    return Subscription(
        id=None,
        client_id=client_id,
        specialist_id=specialist_id,
        purchased=meetings,
        remaining=meetings,
        status=SubscriptionStatus.ACTIVE,
        created_at=datetime.now(UTC),
    )


async def test_add_and_get_active(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make(meetings=5))
    assert saved.id is not None
    active = await repo.get_active(_CLIENT, _SP)
    assert active is not None
    assert active.id == saved.id
    assert active.purchased == 5
    assert active.remaining == 5
    assert active.status is SubscriptionStatus.ACTIVE


async def test_get_active_returns_none_when_absent(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    assert await repo.get_active(_CLIENT, _SP) is None


async def test_get_active_isolated_by_specialist(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    await repo.add(_make())
    # Another specialist must not see this client's subscription.
    assert await repo.get_active(_CLIENT, specialist_id=99) is None


async def test_get_for_specialist_isolation(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    assert await repo.get_for_specialist(saved.id, _SP) is not None
    assert await repo.get_for_specialist(saved.id, specialist_id=99) is None


async def test_update_counters(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make(meetings=8))
    assert saved.id is not None
    updated = await repo.update_counters(saved.id, _SP, purchased=16, remaining=11)
    assert updated is not None
    assert updated.purchased == 16
    assert updated.remaining == 11


async def test_update_counters_unknown_returns_none(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    assert await repo.update_counters(404, _SP, purchased=1, remaining=1) is None


async def test_close_marks_closed_and_drops_from_active(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    closed_at = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    closed = await repo.close(saved.id, _SP, closed_at=closed_at)
    assert closed is not None
    assert closed.status is SubscriptionStatus.CLOSED
    assert closed.closed_at is not None
    # A closed subscription is no longer the active one.
    assert await repo.get_active(_CLIENT, _SP) is None


async def test_close_unknown_returns_none(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    assert await repo.close(404, _SP, closed_at=datetime.now(UTC)) is None


async def test_list_active_for_specialist_isolated(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    await repo.add(_make(client_id=10))
    await repo.add(_make(client_id=11))
    await repo.add(_make(client_id=12, specialist_id=99))  # другой специалист
    active = await repo.list_active_for_specialist(_SP, limit=10, offset=0)
    assert {s.client_id for s in active} == {10, 11}


async def test_list_active_pagination(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    for client_id in range(20, 23):
        await repo.add(_make(client_id=client_id))
    first = await repo.list_active_for_specialist(_SP, limit=2, offset=0)
    second = await repo.list_active_for_specialist(_SP, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 1


async def test_list_closed_for_specialist(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make(client_id=10))
    assert saved.id is not None
    # Активный в список закрытых не попадает.
    assert await repo.list_closed_for_specialist(_SP, limit=10, offset=0) == []
    await repo.close(saved.id, _SP, closed_at=datetime.now(UTC))
    closed = await repo.list_closed_for_specialist(_SP, limit=10, offset=0)
    assert [s.id for s in closed] == [saved.id]


async def test_mark_payment_reminded_sets_and_clears(session: AsyncSession):
    repo = SqlAlchemySubscriptionsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    at = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    await repo.mark_payment_reminded(saved.id, at)
    reloaded = await repo.get_active(_CLIENT, _SP)
    assert reloaded is not None
    assert reloaded.payment_reminded_at is not None
    # Resetting back to None clears the flag (used on extend).
    await repo.mark_payment_reminded(saved.id, None)
    reloaded = await repo.get_active(_CLIENT, _SP)
    assert reloaded is not None
    assert reloaded.payment_reminded_at is None


def test_to_domain_maps_fields():
    orm = SubscriptionORM(
        id=3,
        client_id=_CLIENT,
        specialist_id=_SP,
        purchased=8,
        remaining=4,
        status=SubscriptionStatus.ACTIVE.value,
        created_at=datetime(2026, 6, 6, tzinfo=UTC),
        closed_at=None,
    )
    domain = to_domain(orm)
    assert domain.id == 3
    assert domain.remaining == 4
    assert domain.status is SubscriptionStatus.ACTIVE
    assert "remaining=4" in repr(orm)
