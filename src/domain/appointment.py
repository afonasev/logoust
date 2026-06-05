from dataclasses import dataclass
from datetime import datetime
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


class AppointmentsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(  # pragma: no cover
        self, appointment: Appointment
    ) -> Appointment: ...

    async def get_for_specialist(  # pragma: no cover
        self, appointment_id: int, specialist_id: int
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
