from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.subscription import SubscriptionStatus
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.subscriptions import (
    close_subscription,
    create_subscription,
    decrement_meeting,
    extend_subscription,
    get_active,
    get_card,
    list_active_page,
    list_closed_page,
    parse_meetings,
    parse_presets,
    presets_list,
)

_SP = 1
_CLIENT = 10


def _repo(session: AsyncSession) -> SqlAlchemySubscriptionsRepo:
    return SqlAlchemySubscriptionsRepo(session)


async def test_create_with_explicit_meetings(session: AsyncSession):
    sub = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=4
    )
    assert sub is not None
    assert sub.purchased == 4
    assert sub.remaining == 4
    assert sub.status is SubscriptionStatus.ACTIVE


async def test_create_blocks_second_active(session: AsyncSession):
    await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    second = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert second is None


async def test_get_active_and_get_card(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert created is not None
    assert created.id is not None
    active = await get_active(_repo(session), client_id=_CLIENT, specialist_id=_SP)
    assert active is not None
    assert active.id == created.id
    card = await get_card(_repo(session), subscription_id=created.id, specialist_id=_SP)
    assert card is not None
    assert card.id == created.id


async def test_decrement_lowers_remaining(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=5
    )
    assert created is not None
    assert created.id is not None
    updated = await decrement_meeting(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    assert updated is not None
    assert updated.remaining == 4
    assert updated.status is SubscriptionStatus.ACTIVE


async def test_decrement_does_not_go_below_zero(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=1
    )
    assert created is not None
    assert created.id is not None
    after_one = await decrement_meeting(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    assert after_one is not None
    assert after_one.remaining == 0
    # Second decrement is a no-op: remaining stays 0.
    after_two = await decrement_meeting(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    assert after_two is not None
    assert after_two.remaining == 0


async def test_decrement_unknown_returns_none(session: AsyncSession):
    assert (
        await decrement_meeting(_repo(session), subscription_id=404, specialist_id=_SP)
        is None
    )


async def test_extend_adds_to_both_counters(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert created is not None
    assert created.id is not None
    # Spend five (remaining 3), then extend by 8: purchased 16, remaining 11.
    for _ in range(5):
        await decrement_meeting(
            _repo(session), subscription_id=created.id, specialist_id=_SP
        )
    extended = await extend_subscription(
        _repo(session), subscription_id=created.id, specialist_id=_SP, meetings=8
    )
    assert extended is not None
    assert extended.purchased == 16
    assert extended.remaining == 11


async def test_extend_clears_payment_reminded_flag(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=4
    )
    assert created is not None
    assert created.id is not None
    await _repo(session).mark_payment_reminded(
        created.id, datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    )
    extended = await extend_subscription(
        _repo(session), subscription_id=created.id, specialist_id=_SP, meetings=4
    )
    assert extended is not None
    assert extended.payment_reminded_at is None
    reloaded = await get_active(_repo(session), client_id=_CLIENT, specialist_id=_SP)
    assert reloaded is not None
    assert reloaded.payment_reminded_at is None


async def test_decrement_does_not_touch_payment_reminded_flag(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=2
    )
    assert created is not None
    assert created.id is not None
    at = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    await _repo(session).mark_payment_reminded(created.id, at)
    updated = await decrement_meeting(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    assert updated is not None
    # Decrement leaves the flag untouched.
    assert updated.payment_reminded_at is not None


async def test_extend_unknown_returns_none(session: AsyncSession):
    assert (
        await extend_subscription(
            _repo(session), subscription_id=404, specialist_id=_SP, meetings=8
        )
        is None
    )


async def test_close_with_remaining(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert created is not None
    assert created.id is not None
    closed = await close_subscription(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    assert closed is not None
    assert closed.status is SubscriptionStatus.CLOSED
    assert closed.closed_at is not None
    # After closing the client can get a fresh active subscription.
    again = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert again is not None


async def test_close_unknown_returns_none(session: AsyncSession):
    assert (
        await close_subscription(_repo(session), subscription_id=404, specialist_id=_SP)
        is None
    )


async def test_list_active_page_detects_next(session: AsyncSession):
    for client_id in range(20, 23):
        await create_subscription(
            _repo(session), client_id=client_id, specialist_id=_SP, meetings=8
        )
    first = await list_active_page(
        _repo(session), specialist_id=_SP, page=0, page_size=2
    )
    assert len(first.items) == 2
    assert first.has_next is True
    assert first.has_prev is False
    second = await list_active_page(
        _repo(session), specialist_id=_SP, page=1, page_size=2
    )
    assert len(second.items) == 1
    assert second.has_next is False
    assert second.has_prev is True


async def test_list_closed_page(session: AsyncSession):
    created = await create_subscription(
        _repo(session), client_id=_CLIENT, specialist_id=_SP, meetings=8
    )
    assert created is not None
    assert created.id is not None
    empty = await list_closed_page(
        _repo(session), specialist_id=_SP, page=0, page_size=8
    )
    assert empty.items == []
    await close_subscription(
        _repo(session), subscription_id=created.id, specialist_id=_SP
    )
    closed = await list_closed_page(
        _repo(session), specialist_id=_SP, page=0, page_size=8
    )
    assert [s.id for s in closed.items] == [created.id]


def test_parse_meetings_accepts_positive():
    assert parse_meetings("8") == 8
    assert parse_meetings(" 12 ") == 12


def test_parse_meetings_rejects_invalid():
    assert parse_meetings("0") is None
    assert parse_meetings("-3") is None
    assert parse_meetings("abc") is None
    assert parse_meetings("") is None
    assert parse_meetings("201") is None  # above the upper bound


def test_parse_presets_canonicalises():
    # Sorts, dedups and strips whitespace.
    assert parse_presets("12, 4, 8, 4") == "4,8,12"
    assert parse_presets("8") == "8"


def test_parse_presets_rejects_invalid():
    assert parse_presets("") is None  # empty list
    assert parse_presets("4,abc") is None  # bad element
    assert parse_presets("4,0") is None  # zero is not a valid count
    assert parse_presets("4,,8") is None  # empty element
    # More than _MAX_PRESETS (8) distinct variants.
    assert parse_presets("1,2,3,4,5,6,7,8,9") is None


def test_presets_list_parses_canonical_string():
    assert presets_list("4,8,12") == [4, 8, 12]
    assert presets_list("8") == [8]
