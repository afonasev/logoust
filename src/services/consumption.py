"""Use-case for the daily evening subscription-consumption pass.

The pass (`run_consumption_if_due`) is gated by the pure `is_consumption_due` and,
when due, takes the specialist's *today's* passed occurrences (real rows + virtual
series repeats with `starts_at <= now`), materialises each virtual repeat into a
real row (idempotently, design.md решение 3), and tries to deduct one meeting from
the client's active subscription. The deduction is atomic and idempotent per
appointment (insert-lock + conditional decrement, решение 1), so repeat ticks /
restarts / a manual "send now" over the real pass never double-charge.

The accumulated `ConsumptionReport` (subscriptions charged + ❗ meetings that could
not be charged) is handed to the injected `report` callback — the actual
`send_message` lives in the bot layer. The report is sent only when non-empty; an
empty evening stays silent but the day is still stamped done (решение 6).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import enum
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.client import ClientsRepo
from src.domain.deduction import (
    DeductionOutcome,
    DeductionResult,
    SubscriptionDeductionsRepo,
)
from src.domain.recurring import (
    RecurringScheduleRepo,
    RecurringSlotOverrideRepo,
    RecurringSlotRepo,
)
from src.domain.schedule import today_in_tz
from src.domain.specialist import Specialist, SpecialistsRepo, is_consumption_due
from src.domain.subscription import SubscriptionsRepo
from src.services.appointments import list_specialist_day
from src.services.clients import client_name_map
from src.services.recurring import load_series_context

logger = logging.getLogger(__name__)

_DASH = "—"  # fallback when a client name is unexpectedly missing from the map


class MissReason(enum.Enum):
    """Why a passed meeting could not be auto-deducted."""

    NO_SUBSCRIPTION = "no_subscription"  # client has no active subscription
    EXHAUSTED = "exhausted"  # active subscription but remaining == 0


@dataclass(frozen=True, slots=True)
class DeductedEntry:
    """One subscription charged by the pass — the report shows it as a button."""

    subscription_id: int
    child_name: str
    remaining: int


@dataclass(frozen=True, slots=True)
class MissedEntry:
    """A passed meeting left uncharged — the report shows it as a ❗ line."""

    child_name: str
    starts_at: datetime
    reason: MissReason


@dataclass(frozen=True, slots=True)
class ConsumptionReport:
    deducted: list[DeductedEntry] = field(default_factory=list)
    missed: list[MissedEntry] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.deducted and not self.missed


# report(report) -> awaitable; injected by the bot layer (sends the specialist msg).
ReportFn = Callable[[ConsumptionReport], Awaitable[None]]


async def run_consumption_if_due(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    subscriptions_repo: SubscriptionsRepo,
    deductions_repo: SubscriptionDeductionsRepo,
    clients_repo: ClientsRepo,
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    specialists_repo: SpecialistsRepo,
    report: ReportFn,
) -> None:
    """Run the evening pass when due, report the outcome, stamp the day done."""
    if not is_consumption_due(specialist, now):
        return  # not due → do not stamp; this day still gets its real pass later
    assert specialist.id is not None  # noqa: S101 — candidates are persisted
    result = await run_consumption(
        specialist,
        now,
        appointments_repo=appointments_repo,
        subscriptions_repo=subscriptions_repo,
        deductions_repo=deductions_repo,
        clients_repo=clients_repo,
        schedule_repo=schedule_repo,
        slot_repo=slot_repo,
        override_repo=override_repo,
    )
    if not result.is_empty:
        await report(result)
    # Stamp regardless of outcome — the per-appointment lock prevents re-charges, so
    # the day never re-runs (решение 6).
    await specialists_repo.mark_consumption_run(
        specialist.id, today_in_tz(now, specialist.timezone)
    )


async def run_consumption(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    subscriptions_repo: SubscriptionsRepo,
    deductions_repo: SubscriptionDeductionsRepo,
    clients_repo: ClientsRepo,
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
) -> ConsumptionReport:
    """Deduct today's passed meetings (no gate, no stamp); used also by "send now"."""
    assert specialist.id is not None  # noqa: S101 — caller passes a persisted specialist
    tz = specialist.timezone
    series = await load_series_context(
        schedule_repo,
        slot_repo,
        override_repo,
        specialist_id=specialist.id,
        now=now,
        tz=tz,
    )
    occurrences = await list_specialist_day(
        appointments_repo,
        specialist_id=specialist.id,
        day=today_in_tz(now, tz),
        tz=tz,
        series=series,
    )
    names = await client_name_map(clients_repo, specialist_id=specialist.id)
    report = ConsumptionReport()
    for occ in occurrences:
        if occ.starts_at > now:
            continue  # not yet passed
        await _consume_one(
            specialist,
            occ,
            now,
            names=names,
            appointments_repo=appointments_repo,
            subscriptions_repo=subscriptions_repo,
            deductions_repo=deductions_repo,
            report=report,
        )
    return report


async def _consume_one(  # noqa: PLR0913
    specialist: Specialist,
    occ: Appointment,
    now: datetime,
    *,
    names: dict[int, str],
    appointments_repo: AppointmentsRepo,
    subscriptions_repo: SubscriptionsRepo,
    deductions_repo: SubscriptionDeductionsRepo,
    report: ConsumptionReport,
) -> None:
    assert specialist.id is not None  # noqa: S101 — caller guarantees a persisted id
    child = names.get(occ.client_id, _DASH)
    appointment_id = occ.id
    if appointment_id is None:  # virtual repeat → freeze into a real row first
        appointment_id = await _materialize(appointments_repo, occ, now)
        if appointment_id is None:  # pragma: no cover - insert+read always resolves
            return
    sub = await subscriptions_repo.get_active(occ.client_id, specialist.id)
    if sub is None:
        report.missed.append(
            MissedEntry(child, occ.starts_at, MissReason.NO_SUBSCRIPTION)
        )
        return
    assert sub.id is not None  # noqa: S101 — persisted active subscription
    result = await deductions_repo.add_auto(
        subscription_id=sub.id,
        appointment_id=appointment_id,
        appointment_starts_at=occ.starts_at,
        appointment_comment=occ.comment,
        created_at=now,
    )
    # The outcome → report mapping is a pure sync function: it keeps the branch logic
    # (incl. the silent DUPLICATE no-op) out of this async body, where coverage's
    # tracer fails to record some arcs (Python 3.13 + coverage async blind spot).
    _record_outcome(result, report, subscription_id=sub.id, child=child, occ=occ)
    if result.outcome is DeductionOutcome.DEDUCTED:
        logger.info(
            "subscription.auto_deducted",
            extra={
                "specialist_id": specialist.id,
                "subscription_id": sub.id,
                "appointment_id": appointment_id,
            },
        )


def _record_outcome(
    result: DeductionResult,
    report: ConsumptionReport,
    *,
    subscription_id: int,
    child: str,
    occ: Appointment,
) -> None:
    """Fold one deduction outcome into the report (pure; no I/O).

    DUPLICATE is a deliberate no-op: the meeting was already charged by an
    earlier/concurrent pass and the idempotency lock did its job (design.md, реш. 1).
    """
    if result.outcome is DeductionOutcome.DEDUCTED:
        assert result.remaining is not None  # noqa: S101 — set on DEDUCTED
        report.deducted.append(DeductedEntry(subscription_id, child, result.remaining))
    elif result.outcome is DeductionOutcome.EXHAUSTED:
        report.missed.append(MissedEntry(child, occ.starts_at, MissReason.EXHAUSTED))


async def _materialize(
    appointments_repo: AppointmentsRepo, occ: Appointment, now: datetime
) -> int | None:
    """Freeze a virtual repeat into a real row and return its id (idempotent).

    Uses insert-or-ignore on UNIQUE(slot_id, origin_date) so a concurrent/repeat
    pass cannot create a second row with a different id (design.md, решение 3),
    then re-reads to resolve the real id either pass wrote.
    """
    occurrence = Appointment(
        id=None,
        specialist_id=occ.specialist_id,
        client_id=occ.client_id,
        starts_at=occ.starts_at,
        comment=occ.comment,
        created_at=now,
        updated_at=now,
        slot_id=occ.slot_id,
        origin_date=occ.origin_date,
    )
    await appointments_repo.insert_occurrence(occurrence)
    real = await appointments_repo.find_by_occurrence(
        occ.specialist_id, occ.client_id, starts_at=occ.starts_at
    )
    return real.id if real is not None else None
