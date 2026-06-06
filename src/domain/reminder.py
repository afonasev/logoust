"""Domain entities and repository protocol for appointment reminders.

Pure Python — no SQLAlchemy or aiogram. A *reminder* is a journal row that records
that a client was (or will be) reminded about a single occurrence and what they
answered. An occurrence is identified by the natural key
`(specialist_id, client_id, starts_at)`, so the journal works identically for a
real one-off appointment and a virtual future repeat of a slot, which has no
appointments row (see design.md, decision 1).
"""

from dataclasses import dataclass
from datetime import date, datetime
import enum
from typing import Protocol

from src.domain.schedule import today_in_tz, utc_to_wall
from src.domain.specialist import Specialist


class ReminderStatus(enum.Enum):
    PENDING = "pending"  # sent, awaiting the client's answer
    CONFIRMED = "confirmed"  # client tapped "I'll be there"
    DECLINED = "declined"  # client tapped "I can't make it"


@dataclass(slots=True)
class AppointmentReminder:
    id: int | None
    specialist_id: int
    client_id: int
    starts_at: datetime  # aware UTC; with client_id identifies the occurrence
    # Slot back-reference: both NULL for a one-off appointment, both set for a
    # (possibly virtual) slot occurrence so the "open card" link can be built.
    slot_id: int | None
    origin_date: date | None
    status: ReminderStatus
    sent_at: datetime
    responded_at: datetime | None


def is_reminder_due(specialist: Specialist, now: datetime) -> bool:
    """Whether the daily reminder pass should run for `specialist` at `now`.

    True when reminders are enabled, the specialist's wall-clock time has reached
    `reminder_time`, and the pass has not already run today (in their timezone).
    The `>=` threshold gives a catch-up after downtime; `reminder_last_run_on` is a
    cheap guard — the journal's UNIQUE key is the real no-duplicate guarantee.
    """
    if not specialist.reminder_enabled:
        return False
    tz = specialist.timezone
    if specialist.reminder_last_run_on == today_in_tz(now, tz):
        return False
    # Both sides are zero-padded "HH:MM", so a lexical compare is a time compare.
    return f"{utc_to_wall(now, tz):%H:%M}" >= specialist.reminder_time


class RemindersRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def insert_pending(  # pragma: no cover
        self, reminder: AppointmentReminder
    ) -> bool: ...

    async def get(  # pragma: no cover
        self, reminder_id: int
    ) -> AppointmentReminder | None: ...

    async def set_status(  # pragma: no cover
        self,
        reminder_id: int,
        status: ReminderStatus,
        responded_at: datetime,
    ) -> ReminderStatus | None: ...

    async def statuses_for_day(  # pragma: no cover
        self,
        specialist_id: int,
        occurrences: list[tuple[int, datetime]],
    ) -> dict[tuple[int, datetime], ReminderStatus]: ...
