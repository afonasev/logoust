"""Domain entities and repository protocols for weekly recurring schedules.

Pure Python — no SQLAlchemy or aiogram. A client's regular *schedule*
(`RecurringSchedule`) owns any number of *slots* (`RecurringSlot`), each a weekly
rule that repeats on `weekday` at wall-clock `time_hhmm` (specialist timezone),
starting at `start_date`, forever while both the slot and its schedule are active
(see design.md, decision 1). Past occurrences of a slot are frozen into real
`appointments` rows keyed by `(slot_id, origin_date)`; future ones are computed
from the rule. A *slot override* (`RecurringSlotOverride`) tweaks a single dated
occurrence along three independent axes: skip it, move it, or re-comment it
(design.md, decision 3).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(slots=True)
class RecurringSchedule:
    id: int | None
    specialist_id: int
    client_id: int
    comment: str | None  # shared default comment inherited by every occurrence
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RecurringSlot:
    id: int | None
    schedule_id: int
    weekday: int  # 0=Mon … 6=Sun (date.weekday convention)
    time_hhmm: str  # wall-clock "HH:MM" in the specialist's timezone
    active: bool
    # First occurrence ≥ creation day; anchors the weekly grid (every grid date
    # shares this weekday, so stepping by 7 days preserves it — DST-safe per date).
    start_date: date
    # Past occurrences are frozen into rows for dates < this day; settle advances it.
    materialized_through: date
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RecurringSlotOverride:
    id: int | None
    slot_id: int
    original_date: date  # the planned grid date this override affects
    skipped: bool  # True = the occurrence is cancelled (no meeting)
    moved_to: datetime | None  # set = move to this UTC instant; None = grid time
    comment: str | None  # set = overrides schedule.comment for this occurrence
    created_at: datetime


class RecurringScheduleRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(  # pragma: no cover
        self, schedule: RecurringSchedule
    ) -> RecurringSchedule: ...

    async def list_active_for_specialist(  # pragma: no cover
        self, specialist_id: int
    ) -> list[RecurringSchedule]: ...

    async def get_for_specialist(  # pragma: no cover
        self, schedule_id: int, specialist_id: int
    ) -> RecurringSchedule | None: ...

    async def set_active(  # pragma: no cover
        self,
        schedule_id: int,
        specialist_id: int,
        *,
        active: bool,
        updated_at: datetime,
    ) -> RecurringSchedule | None: ...


class RecurringSlotRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(self, slot: RecurringSlot) -> RecurringSlot: ...  # pragma: no cover

    async def list_for_schedule(  # pragma: no cover
        self, schedule_id: int
    ) -> list[RecurringSlot]: ...

    async def get_for_specialist(  # pragma: no cover
        self, slot_id: int, specialist_id: int
    ) -> RecurringSlot | None: ...

    async def set_active(  # pragma: no cover
        self, slot_id: int, specialist_id: int, *, active: bool, updated_at: datetime
    ) -> RecurringSlot | None: ...

    async def set_materialized_through(  # pragma: no cover
        self, slot_id: int, *, materialized_through: date
    ) -> None: ...

    async def update_rule(  # noqa: PLR0913  # pragma: no cover
        self,
        slot_id: int,
        specialist_id: int,
        *,
        weekday: int,
        time_hhmm: str,
        start_date: date,
        materialized_through: date,
        updated_at: datetime,
    ) -> RecurringSlot | None: ...


class RecurringSlotOverrideRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def upsert(  # noqa: PLR0913  # pragma: no cover
        self,
        slot_id: int,
        original_date: date,
        *,
        skipped: bool,
        moved_to: datetime | None,
        comment: str | None,
        created_at: datetime,
    ) -> RecurringSlotOverride: ...

    async def list_for_slot(  # pragma: no cover
        self, slot_id: int
    ) -> list[RecurringSlotOverride]: ...

    async def list_for_specialist(  # pragma: no cover
        self, specialist_id: int
    ) -> list[RecurringSlotOverride]: ...
