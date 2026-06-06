from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from src.domain.schedule import today_in_tz, utc_to_wall

# Defaults for a freshly invited specialist; also the migration's server-defaults.
DEFAULT_TIMEZONE = "Asia/Yekaterinburg"
DEFAULT_DAY_START = "09:00"
DEFAULT_DAY_END = "20:00"
DEFAULT_SLOT_MINUTES = 60
# Canonical sorted weekday indices (Mon=0…Sun=6); default working days are Mon-Fri.
DEFAULT_WORKING_DAYS = "0,1,2,3,4"
# Appointment reminders are opt-out (on by default) and fire at noon wall-time.
DEFAULT_REMINDER_ENABLED = True
DEFAULT_REMINDER_TIME = "12:00"
# The specialist's morning digest is opt-out (on by default) and fires at 10:00
# wall-time in their timezone.
DEFAULT_MORNING_NOTIFY_ENABLED = True
DEFAULT_MORNING_NOTIFY_TIME = "10:00"
# The payment reminder pass is opt-out (on by default) and fires at noon wall-time
# in their timezone.
DEFAULT_PAYMENT_REMINDER_ENABLED = True
DEFAULT_PAYMENT_REMINDER_TIME = "12:00"
# Варианты числа встреч (кнопки) при создании/продлении абонемента — список
# через запятую, канонизированный (по возрастанию, без повторов).
DEFAULT_SUBSCRIPTION_PRESETS = "4,8,12"
# Время кнопки-пресета при откладывании уведомления клиенту (настенное "HH:MM" в
# таймзоне специалиста); также server-default миграции.
DEFAULT_DEFERRED_NOTIFY_TIME = "20:00"


class ChatIdConflictError(Exception):
    """Raised when binding a chat_id collides with another specialist."""


@dataclass(slots=True)
class Specialist:
    id: int | None
    invite_token: str
    telegram_chat_id: int | None
    telegram_username: str | None
    welcomed_at: datetime | None
    created_at: datetime
    # Schedule settings drive slot generation and wall-time ↔ UTC conversion.
    timezone: str = DEFAULT_TIMEZONE
    day_start: str = DEFAULT_DAY_START
    day_end: str = DEFAULT_DAY_END
    slot_minutes: int = DEFAULT_SLOT_MINUTES
    # Canonical sorted weekday indices (Mon=0…Sun=6), e.g. "0,1,2,3,4".
    working_days: str = DEFAULT_WORKING_DAYS
    # Daily client-reminder pass: on/off, wall-clock "HH:MM" trigger, and the last
    # day (in tz) the pass ran — the anti-duplicate / catch-up guard.
    reminder_enabled: bool = DEFAULT_REMINDER_ENABLED
    reminder_time: str = DEFAULT_REMINDER_TIME
    reminder_last_run_on: date | None = None
    # Daily morning digest to the specialist: on/off, wall-clock "HH:MM" trigger, and
    # the last day (in tz) the pass ran — the anti-duplicate / catch-up guard.
    morning_notify_enabled: bool = DEFAULT_MORNING_NOTIFY_ENABLED
    morning_notify_time: str = DEFAULT_MORNING_NOTIFY_TIME
    morning_notify_last_run_on: date | None = None
    # Daily payment-reminder pass: on/off, wall-clock "HH:MM" trigger, and the last
    # day (in tz) the pass ran — the anti-duplicate / catch-up guard.
    payment_reminder_enabled: bool = DEFAULT_PAYMENT_REMINDER_ENABLED
    payment_reminder_time: str = DEFAULT_PAYMENT_REMINDER_TIME
    payment_reminder_last_run_on: date | None = None
    # Дефолтное число встреч в абонементе, подставляемое в подсказку создания/продления.
    subscription_presets: str = DEFAULT_SUBSCRIPTION_PRESETS
    # Настенное "HH:MM" кнопки-пресета при откладывании уведомления клиенту.
    deferred_notify_time: str = DEFAULT_DEFERRED_NOTIFY_TIME


def is_digest_due(specialist: "Specialist", now: datetime) -> bool:
    """Whether the morning digest pass should run for `specialist` at `now`.

    True when the digest is enabled, the specialist's wall-clock time has reached
    `morning_notify_time`, and the pass has not already run today (in their
    timezone). The `>=` threshold gives a catch-up after downtime; the day-stamp
    guard makes repeat ticks and restarts no-ops (see design.md, decision 2).
    """
    if not specialist.morning_notify_enabled:
        return False
    tz = specialist.timezone
    if specialist.morning_notify_last_run_on == today_in_tz(now, tz):
        return False
    # Both sides are zero-padded "HH:MM", so a lexical compare is a time compare.
    return f"{utc_to_wall(now, tz):%H:%M}" >= specialist.morning_notify_time


def is_payment_reminder_due(specialist: "Specialist", now: datetime) -> bool:
    """Whether the payment-reminder pass should run for `specialist` at `now`.

    True when the reminder is enabled, the specialist's wall-clock time has reached
    `payment_reminder_time`, and the pass has not already run today (in their
    timezone). The `>=` threshold gives a catch-up after downtime; the day-stamp
    guard makes repeat ticks and restarts no-ops (see design.md, decision 3).
    """
    if not specialist.payment_reminder_enabled:
        return False
    tz = specialist.timezone
    if specialist.payment_reminder_last_run_on == today_in_tz(now, tz):
        return False
    # Both sides are zero-padded "HH:MM", so a lexical compare is a time compare.
    return f"{utc_to_wall(now, tz):%H:%M}" >= specialist.payment_reminder_time


class SpecialistsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(self, specialist: Specialist) -> Specialist: ...  # pragma: no cover

    async def find_by_token(  # pragma: no cover
        self, token: str
    ) -> Specialist | None: ...

    async def find_by_chat_id(  # pragma: no cover
        self, chat_id: int
    ) -> Specialist | None: ...

    async def mark_welcomed(  # pragma: no cover
        self,
        specialist_id: int,
        *,
        telegram_chat_id: int,
        telegram_username: str | None,
        welcomed_at: datetime,
    ) -> None: ...

    async def get(  # pragma: no cover
        self, specialist_id: int
    ) -> Specialist | None: ...

    async def update_settings(  # pragma: no cover
        self, specialist_id: int, fields: Mapping[str, object]
    ) -> Specialist | None: ...

    async def list_reminder_candidates(  # pragma: no cover
        self,
    ) -> list[Specialist]: ...

    async def mark_reminder_run(  # pragma: no cover
        self, specialist_id: int, run_on: date
    ) -> None: ...

    async def list_digest_candidates(  # pragma: no cover
        self,
    ) -> list[Specialist]: ...

    async def mark_digest_run(  # pragma: no cover
        self, specialist_id: int, run_on: date
    ) -> None: ...

    async def list_payment_reminder_candidates(  # pragma: no cover
        self,
    ) -> list[Specialist]: ...

    async def mark_payment_reminder_run(  # pragma: no cover
        self, specialist_id: int, run_on: date
    ) -> None: ...
