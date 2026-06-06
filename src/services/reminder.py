"""Use-cases for the daily client reminder: the pass and the client's response.

The pass (`run_reminders_if_due`) is gated by the pure `is_reminder_due` and, when
due, reminds every linked client with an appointment tomorrow (real or a virtual
series repeat), journaling each so a repeat tick never re-sends. It returns the
messages to deliver — the actual `send_message` is the bot layer's job, keeping
aiogram out of services. `apply_reminder_response` records the client's answer and
signals when the specialist must be told about a decline.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.client import Client, ClientsRepo, ClientStatus
from src.domain.recurring import (
    RecurringScheduleRepo,
    RecurringSlotOverrideRepo,
    RecurringSlotRepo,
)
from src.domain.reminder import (
    AppointmentReminder,
    RemindersRepo,
    ReminderStatus,
    is_reminder_due,
)
from src.domain.schedule import format_ru_date, today_in_tz, utc_to_wall
from src.domain.specialist import Specialist, SpecialistsRepo
from src.services.appointments import list_specialist_day
from src.services.recurring import load_series_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReminderMessages:
    """Reminder texts pulled from messages.toml, threaded into the service."""

    # Resolved per specialist (override ?? default); depersonalized — {date} {time}
    # only, no child name. An extra child= kwarg at format time is simply ignored.
    client_text: str


@dataclass(slots=True)
class ReminderToSend:
    reminder_id: int
    chat_id: int
    text: str
    specialist_id: int
    client_id: int


@dataclass(slots=True)
class ReminderResponse:
    reminder: AppointmentReminder
    # True only on a transition into `declined` — the specialist is told once.
    notify_specialist: bool


def render_reminder_text(
    occurrence: Appointment, child: str, tz: str, messages: ReminderMessages
) -> str:
    wall = utc_to_wall(occurrence.starts_at, tz)
    return messages.client_text.format(
        child=child, date=format_ru_date(wall.date()), time=f"{wall:%H:%M}"
    )


async def run_reminders_if_due(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    reminders_repo: RemindersRepo,
    specialists_repo: SpecialistsRepo,
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    clients_repo: ClientsRepo,
    messages: ReminderMessages,
) -> list[ReminderToSend]:
    """Remind linked clients of tomorrow's appointments when the pass is due."""
    if not is_reminder_due(specialist, now):
        return []
    assert specialist.id is not None  # noqa: S101 — candidates are persisted
    tz = specialist.timezone
    today = today_in_tz(now, tz)
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
        day=today + timedelta(days=1),
        tz=tz,
        series=series,
    )
    clients = {
        c.id: c
        for c in await clients_repo.list_by_status(specialist.id, ClientStatus.ACTIVE)
        if c.id is not None
    }
    to_send = await _journal_and_collect(
        occurrences, clients, specialist, now, tz, reminders_repo, messages
    )
    # Mark the day done regardless of send outcomes — the journal prevents re-sends.
    await specialists_repo.mark_reminder_run(specialist.id, today)
    return to_send


async def _journal_and_collect(  # noqa: PLR0913, PLR0917
    occurrences: list[Appointment],
    clients: dict[int, Client],
    specialist: Specialist,
    now: datetime,
    tz: str,
    reminders_repo: RemindersRepo,
    messages: ReminderMessages,
) -> list[ReminderToSend]:
    assert specialist.id is not None  # noqa: S101 — caller guarantees a persisted id
    to_send: list[ReminderToSend] = []
    for occ in occurrences:
        client = clients.get(occ.client_id)
        if client is None or client.telegram_chat_id is None:
            continue  # unlinked client (or archived) — nothing to send
        reminder = AppointmentReminder(
            id=None,
            specialist_id=specialist.id,
            client_id=occ.client_id,
            starts_at=occ.starts_at,
            slot_id=occ.slot_id,
            origin_date=occ.origin_date,
            status=ReminderStatus.PENDING,
            sent_at=now,
            responded_at=None,
        )
        if not await reminders_repo.insert_pending(reminder):
            continue  # already reminded for this occurrence
        assert reminder.id is not None  # noqa: S101 — set on a successful insert
        to_send.append(
            ReminderToSend(
                reminder_id=reminder.id,
                chat_id=client.telegram_chat_id,
                text=render_reminder_text(occ, client.child_name, tz, messages),
                specialist_id=specialist.id,
                client_id=occ.client_id,
            )
        )
    return to_send


async def apply_reminder_response(  # noqa: PLR0913
    reminders_repo: RemindersRepo,
    clients_repo: ClientsRepo,
    *,
    reminder_id: int,
    chat_id: int,
    confirm: bool,
    now: datetime,
) -> ReminderResponse | None:
    """Record the client's answer; return None if it is not theirs to answer.

    Isolation: only the client this reminder was sent to may answer it. `chat_id`
    is non-unique across cards, so we match the reminder's specific client.
    """
    reminder = await reminders_repo.get(reminder_id)
    if reminder is None:
        return None
    client = await clients_repo.get_for_specialist(
        reminder.client_id, reminder.specialist_id
    )
    if client is None or client.telegram_chat_id != chat_id:
        return None
    new_status = ReminderStatus.CONFIRMED if confirm else ReminderStatus.DECLINED
    previous = await reminders_repo.set_status(reminder_id, new_status, now)
    notify = (
        new_status is ReminderStatus.DECLINED
        and previous is not ReminderStatus.DECLINED
    )
    event = (
        "appointment.reminder_confirmed" if confirm else "appointment.reminder_declined"
    )
    logger.info(
        event,
        extra={
            "specialist_id": reminder.specialist_id,
            "client_id": reminder.client_id,
        },
    )
    return ReminderResponse(reminder=reminder, notify_specialist=notify)


async def status_for_occurrence(
    reminders_repo: RemindersRepo,
    *,
    specialist_id: int,
    client_id: int,
    starts_at: datetime,
) -> ReminderStatus | None:
    """Confirmation status of a single occurrence, or None when not reminded."""
    statuses = await reminders_repo.statuses_for_day(
        specialist_id, [(client_id, starts_at)]
    )
    return statuses.get((client_id, starts_at))


async def statuses_for_appointments(
    reminders_repo: RemindersRepo,
    *,
    specialist_id: int,
    appointments: list[Appointment],
) -> dict[tuple[int, datetime], ReminderStatus]:
    """Statuses keyed by `(client_id, starts_at)` for a day's appointments."""
    occurrences = [(a.client_id, a.starts_at) for a in appointments]
    return await reminders_repo.statuses_for_day(specialist_id, occurrences)
