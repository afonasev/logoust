from dataclasses import dataclass
from datetime import datetime
import enum
from typing import Protocol


class SubscriptionStatus(enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(slots=True)
class Subscription:
    id: int | None
    client_id: int
    specialist_id: int
    # purchased — всего куплено за жизнь абонемента (растёт при продлении);
    # remaining — текущий остаток. См. design.md, решение 4.
    purchased: int
    remaining: int
    status: SubscriptionStatus
    created_at: datetime
    closed_at: datetime | None = None
    # Anti-duplicate: момент, когда по абонементу уже сформировали напоминание в
    # текущем пустом цикле. Сбрасывается при продлении (remaining 0 → >0).
    # См. design.md, решение 3.
    payment_reminded_at: datetime | None = None


def subscription_needs_payment_reminder(sub: Subscription) -> bool:
    """Нужно ли по абонементу напоминание (payment reminder).

    True, когда абонемент активен, остаток исчерпан и в текущем пустом цикле
    напоминание ещё не формировалось (см. design.md, решение 3).
    """
    return (
        sub.status is SubscriptionStatus.ACTIVE
        and sub.remaining == 0
        and sub.payment_reminded_at is None
    )


class SubscriptionsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(  # pragma: no cover
        self, subscription: Subscription
    ) -> Subscription: ...

    async def get_active(  # pragma: no cover
        self, client_id: int, specialist_id: int
    ) -> Subscription | None: ...

    async def list_active_for_specialist(  # pragma: no cover
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Subscription]: ...

    async def list_closed_for_specialist(  # pragma: no cover
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Subscription]: ...

    async def get_for_specialist(  # pragma: no cover
        self, subscription_id: int, specialist_id: int
    ) -> Subscription | None: ...

    async def update_counters(  # pragma: no cover
        self,
        subscription_id: int,
        specialist_id: int,
        *,
        purchased: int,
        remaining: int,
    ) -> Subscription | None: ...

    async def close(  # pragma: no cover
        self, subscription_id: int, specialist_id: int, *, closed_at: datetime
    ) -> Subscription | None: ...

    async def mark_payment_reminded(  # pragma: no cover
        self, subscription_id: int, at: datetime | None
    ) -> None: ...
