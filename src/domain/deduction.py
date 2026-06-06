from dataclasses import dataclass
from datetime import datetime
import enum
from typing import Protocol

from src.domain.subscription import Subscription


@dataclass(slots=True)
class SubscriptionDeduction:
    """One row of a subscription's deduction journal.

    Auto-deductions carry a back-reference to the appointment they paid for
    (`appointment_id`) plus *snapshots* of its instant and booking comment, so the
    journal stays self-sufficient and immutable-by-fact even if the appointment is
    later edited or deleted (design.md, decision 5). Manual deductions have
    `appointment_id IS NULL` and no snapshots. `closing_comment` — the editable
    "after the meeting" note — is the only mutable field. A cancelled row keeps
    `cancelled_at` set and stays in the table to hold the idempotency lock
    (decision 4).
    """

    id: int | None
    subscription_id: int
    appointment_id: int | None
    appointment_starts_at: datetime | None
    appointment_comment: str | None
    closing_comment: str | None
    created_at: datetime
    cancelled_at: datetime | None = None


class DeductionOutcome(enum.Enum):
    """Result of an auto-deduction attempt for one appointment."""

    DEDUCTED = "deducted"  # remaining decremented, journal row written
    EXHAUSTED = "exhausted"  # active subscription but remaining was 0
    DUPLICATE = "duplicate"  # this appointment was already deducted (lock hit)


@dataclass(frozen=True, slots=True)
class DeductionResult:
    outcome: DeductionOutcome
    # Set only when outcome is DEDUCTED.
    deduction: SubscriptionDeduction | None = None
    remaining: int | None = None


class SubscriptionDeductionsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add_auto(  # pragma: no cover
        self,
        *,
        subscription_id: int,
        appointment_id: int,
        appointment_starts_at: datetime,
        appointment_comment: str | None,
        created_at: datetime,
    ) -> DeductionResult: ...

    async def add_manual(  # pragma: no cover
        self, *, subscription_id: int, specialist_id: int, created_at: datetime
    ) -> SubscriptionDeduction | None: ...

    async def list_active_for_subscription(  # pragma: no cover
        self, subscription_id: int
    ) -> list[SubscriptionDeduction]: ...

    async def get_for_specialist(  # pragma: no cover
        self, deduction_id: int, specialist_id: int
    ) -> SubscriptionDeduction | None: ...

    async def set_closing_comment(  # pragma: no cover
        self, deduction_id: int, specialist_id: int, *, comment: str | None
    ) -> SubscriptionDeduction | None: ...

    async def cancel(  # pragma: no cover
        self, deduction_id: int, specialist_id: int, *, cancelled_at: datetime
    ) -> Subscription | None: ...

    async def exists_for_appointment(  # pragma: no cover
        self, appointment_id: int
    ) -> bool: ...
