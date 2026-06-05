"""Domain entities and repository protocols for weekly recurring appointments.

Pure Python — no SQLAlchemy or aiogram. A *series* is a rule that repeats every
week on `weekday` at wall-clock `time_hhmm` (specialist timezone), starting at
`start_date`, forever while `active`. Past occurrences are frozen into real
`appointments` rows; future ones are computed from the rule (see design.md,
decision 1). An *exception* suppresses or moves a single dated occurrence.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(slots=True)
class RecurringAppointment:
    id: int | None
    specialist_id: int
    client_id: int
    weekday: int  # 0=Mon … 6=Sun (date.weekday convention)
    time_hhmm: str  # wall-clock "HH:MM" in the specialist's timezone
    comment: str | None
    active: bool
    # First occurrence ≥ creation day; anchors the weekly grid (every grid date
    # shares this weekday, so stepping by 7 days preserves it — DST-safe per date).
    start_date: date
    # Past occurrences are frozen into rows for dates < this day; settle advances it.
    materialized_through: date
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RecurringException:
    id: int | None
    series_id: int
    original_date: date  # the planned grid date this exception affects
    # None = skip the date entirely; set = move the occurrence to this UTC instant.
    new_starts_at: datetime | None
    created_at: datetime


class RecurringRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(  # pragma: no cover
        self, series: RecurringAppointment
    ) -> RecurringAppointment: ...

    async def list_active_for_specialist(  # pragma: no cover
        self, specialist_id: int
    ) -> list[RecurringAppointment]: ...

    async def get_for_specialist(  # pragma: no cover
        self, series_id: int, specialist_id: int
    ) -> RecurringAppointment | None: ...

    async def set_active(  # pragma: no cover
        self, series_id: int, specialist_id: int, *, active: bool, updated_at: datetime
    ) -> RecurringAppointment | None: ...

    async def set_materialized_through(  # pragma: no cover
        self, series_id: int, *, materialized_through: date
    ) -> None: ...

    async def update_rule(  # noqa: PLR0913  # pragma: no cover
        self,
        series_id: int,
        specialist_id: int,
        *,
        weekday: int,
        time_hhmm: str,
        comment: str | None,
        start_date: date,
        materialized_through: date,
        updated_at: datetime,
    ) -> RecurringAppointment | None: ...


class RecurringExceptionsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def upsert(  # pragma: no cover
        self,
        series_id: int,
        original_date: date,
        *,
        new_starts_at: datetime | None,
        created_at: datetime,
    ) -> RecurringException: ...

    async def list_for_series(  # pragma: no cover
        self, series_id: int
    ) -> list[RecurringException]: ...

    async def list_for_specialist(  # pragma: no cover
        self, specialist_id: int
    ) -> list[RecurringException]: ...
