"""Use-case for the daily payment-reminder pass.

The pass (`run_payment_reminders_if_due`) is gated by the pure
`is_payment_reminder_due` and, when due, looks at tomorrow's appointments (real or
virtual series repeats), groups them by client, and for each client whose active
subscription is exhausted (`remaining == 0`, not yet reminded in this empty cycle)
emits one alert to the specialist via the injected `alert` callback. The flag is
stamped per subscription so the same empty cycle never re-alerts, and the day is
stamped per specialist so a repeat tick / restart is a no-op. The actual
`send_message` lives in the bot layer — services stay free of aiogram.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.client import ClientsRepo
from src.domain.recurring import (
    RecurringScheduleRepo,
    RecurringSlotOverrideRepo,
    RecurringSlotRepo,
)
from src.domain.schedule import today_in_tz
from src.domain.specialist import (
    Specialist,
    SpecialistsRepo,
    is_payment_reminder_due,
)
from src.domain.subscription import (
    SubscriptionsRepo,
    subscription_needs_payment_reminder,
)
from src.services.appointments import list_specialist_day
from src.services.recurring import load_series_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaymentReminderAlert:
    """Data the bot layer needs to build the specialist message + send button.

    `chat_id` is the client's `telegram_chat_id`; None means the client is not
    linked, so no "send" button is shown. `starts_at` is the earliest tomorrow
    appointment instant (aware UTC) — the bot converts it to the specialist's
    wall-time for the message.
    """

    client_id: int
    child_name: str
    chat_id: int | None
    starts_at: datetime


# alert(payload) -> awaitable; injected by the bot layer (sends the specialist msg).
AlertFn = Callable[[PaymentReminderAlert], Awaitable[None]]


async def run_payment_reminders_if_due(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    subscriptions_repo: SubscriptionsRepo,
    clients_repo: ClientsRepo,
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    specialists_repo: SpecialistsRepo,
    alert: AlertFn,
) -> None:
    """Alert the specialist about exhausted subscriptions for tomorrow's clients."""
    if not is_payment_reminder_due(specialist, now):
        return  # not due → do not stamp; this day still gets its real pass later
    assert specialist.id is not None  # noqa: S101 — candidates are persisted
    tz = specialist.timezone
    today = today_in_tz(now, tz)
    earliest = await _earliest_by_client(
        specialist,
        now,
        tz,
        appointments_repo=appointments_repo,
        schedule_repo=schedule_repo,
        slot_repo=slot_repo,
        override_repo=override_repo,
    )
    for client_id, occ in earliest.items():
        await _alert_if_needed(
            specialist,
            client_id,
            occ,
            now,
            subscriptions_repo=subscriptions_repo,
            clients_repo=clients_repo,
            alert=alert,
        )
    # Mark the day done regardless of outcomes — the per-subscription flag (and the
    # day stamp) prevent re-alerts (see design.md, decision 3).
    await specialists_repo.mark_payment_reminder_run(specialist.id, today)


async def _earliest_by_client(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    tz: str,
    *,
    appointments_repo: AppointmentsRepo,
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
) -> dict[int, Appointment]:
    """Tomorrow's occurrences grouped by client → the earliest one per client."""
    assert specialist.id is not None  # noqa: S101 — caller guarantees a persisted id
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
        day=today_in_tz(now, tz) + timedelta(days=1),
        tz=tz,
        series=series,
    )
    # occurrences come sorted by starts_at, so the first seen per client is earliest.
    earliest: dict[int, Appointment] = {}
    for occ in occurrences:
        earliest.setdefault(occ.client_id, occ)
    return earliest


async def _alert_if_needed(  # noqa: PLR0913
    specialist: Specialist,
    client_id: int,
    occ: Appointment,
    now: datetime,
    *,
    subscriptions_repo: SubscriptionsRepo,
    clients_repo: ClientsRepo,
    alert: AlertFn,
) -> None:
    assert specialist.id is not None  # noqa: S101 — caller guarantees a persisted id
    sub = await subscriptions_repo.get_active(client_id, specialist.id)
    if sub is None or not subscription_needs_payment_reminder(sub):
        return
    client = await clients_repo.get_for_specialist(client_id, specialist.id)
    if client is None:  # pragma: no cover - occurrence implies an existing client
        return
    await alert(
        PaymentReminderAlert(
            client_id=client_id,
            child_name=client.child_name,
            chat_id=client.telegram_chat_id,
            starts_at=occ.starts_at,
        )
    )
    assert sub.id is not None  # noqa: S101 — persisted active subscription
    await subscriptions_repo.mark_payment_reminded(sub.id, now)
    logger.info(
        "subscription.payment_reminder_alerted",
        extra={
            "specialist_id": specialist.id,
            "client_id": client_id,
            "subscription_id": sub.id,
            "linked": client.telegram_chat_id is not None,
        },
    )
