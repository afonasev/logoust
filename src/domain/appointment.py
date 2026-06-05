from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(slots=True)
class Appointment:
    id: int | None
    specialist_id: int
    client_id: int
    starts_at: datetime  # aware UTC
    comment: str | None
    created_at: datetime
    updated_at: datetime
    # Recurring-series back-reference. Both NULL = a one-off appointment. A real
    # row with both set is a materialised past occurrence; a virtual future
    # occurrence is an Appointment with id=None and these set (see design.md).
    series_id: int | None = None
    origin_date: date | None = None
    # Transient display flag (never persisted): True only for a plain future series
    # occurrence, so lists show the 🔁 marker. A moved/rescheduled single date is
    # individualised and carries False, so it is not marked as recurring.
    recurring_mark: bool = False


class AppointmentsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(  # pragma: no cover
        self, appointment: Appointment
    ) -> Appointment: ...

    async def get_for_specialist(  # pragma: no cover
        self, appointment_id: int, specialist_id: int
    ) -> Appointment | None: ...

    async def find_by_occurrence(  # pragma: no cover
        self, specialist_id: int, client_id: int, *, starts_at: datetime
    ) -> Appointment | None: ...

    async def list_future_for_specialist(  # pragma: no cover
        self, specialist_id: int, *, since: datetime
    ) -> list[Appointment]: ...

    async def list_for_specialist_between(  # pragma: no cover
        self, specialist_id: int, *, start: datetime, end: datetime
    ) -> list[Appointment]: ...

    async def list_past_for_specialist(  # pragma: no cover
        self, specialist_id: int, *, before: datetime, limit: int, offset: int
    ) -> list[Appointment]: ...

    async def list_future_for_client(  # pragma: no cover
        self, specialist_id: int, client_id: int, *, since: datetime
    ) -> list[Appointment]: ...

    async def list_past_for_client(  # pragma: no cover
        self,
        specialist_id: int,
        client_id: int,
        *,
        before: datetime,
        limit: int,
        offset: int,
    ) -> list[Appointment]: ...

    async def update_starts_at(  # pragma: no cover
        self,
        appointment_id: int,
        specialist_id: int,
        *,
        starts_at: datetime,
        updated_at: datetime,
    ) -> Appointment | None: ...

    async def delete(  # pragma: no cover
        self, appointment_id: int, specialist_id: int
    ) -> bool: ...

    async def insert_occurrence(  # pragma: no cover
        self, occurrence: "Appointment"
    ) -> bool: ...
