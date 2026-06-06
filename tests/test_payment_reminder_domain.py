from datetime import UTC, date, datetime

import pytest

from src.domain.specialist import Specialist, is_payment_reminder_due
from src.domain.subscription import (
    Subscription,
    SubscriptionStatus,
    subscription_needs_payment_reminder,
)

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST


def _specialist(**overrides: object) -> Specialist:
    base: dict[str, object] = {
        "id": 1,
        "invite_token": "t",
        "telegram_chat_id": 100,
        "telegram_username": None,
        "welcomed_at": None,
        "created_at": datetime.now(UTC),
        "timezone": _TZ,
        "payment_reminder_time": "12:00",
    }
    base.update(overrides)
    return Specialist(**base)  # type: ignore[arg-type]


def test_due_at_reminder_time():
    # 07:00 UTC → 12:00 wall in Yekaterinburg, exactly payment_reminder_time.
    now = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    assert is_payment_reminder_due(_specialist(), now) is True


def test_not_due_before_reminder_time():
    # 06:00 UTC → 11:00 wall, before 12:00.
    now = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
    assert is_payment_reminder_due(_specialist(), now) is False


def test_not_due_when_already_run_today():
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)  # 13:00 wall
    today = date(2026, 6, 15)
    assert (
        is_payment_reminder_due(_specialist(payment_reminder_last_run_on=today), now)
        is False
    )


def test_not_due_when_disabled():
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    assert (
        is_payment_reminder_due(_specialist(payment_reminder_enabled=False), now)
        is False
    )


def test_catch_up_after_downtime_same_day():
    # Bot was down at noon, wakes at 15:00 wall (10:00 UTC); last run was yesterday.
    now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    specialist = _specialist(payment_reminder_last_run_on=date(2026, 6, 14))
    assert is_payment_reminder_due(specialist, now) is True


def _sub(
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    remaining: int = 0,
    reminded_at: datetime | None = None,
) -> Subscription:
    return Subscription(
        id=1,
        client_id=10,
        specialist_id=1,
        purchased=8,
        remaining=remaining,
        status=status,
        created_at=datetime.now(UTC),
        payment_reminded_at=reminded_at,
    )


@pytest.mark.parametrize(
    ("status", "remaining", "reminded_at", "expected"),
    [
        (SubscriptionStatus.ACTIVE, 0, None, True),
        (SubscriptionStatus.ACTIVE, 0, datetime.now(UTC), False),
        (SubscriptionStatus.ACTIVE, 1, None, False),
        (SubscriptionStatus.ACTIVE, 1, datetime.now(UTC), False),
        (SubscriptionStatus.CLOSED, 0, None, False),
        (SubscriptionStatus.CLOSED, 0, datetime.now(UTC), False),
        (SubscriptionStatus.CLOSED, 1, None, False),
    ],
)
def test_subscription_needs_payment_reminder(status, remaining, reminded_at, expected):
    sub = _sub(status=status, remaining=remaining, reminded_at=reminded_at)
    assert subscription_needs_payment_reminder(sub) is expected
