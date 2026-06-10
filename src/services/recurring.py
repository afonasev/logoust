"""Use-cases for multi-slot recurring schedules: create, configure, expand, settle.

A client's *schedule* owns any number of weekly *slots* (day-of-week + time). The
split between materialised past and computed future lives per slot (design.md,
decision 1): `occurrences_landing_in` turns one slot's rule into virtual
`Appointment`s for reading; `settle` freezes passed occurrences into real rows on
specialist interaction. A virtual occurrence is an `Appointment` with `id=None` and
`slot_id`/`origin_date` set, so the schedule screens merge it with real rows
uniformly. Each occurrence carries its *effective comment* — the slot's per-date
override comment if set, else the schedule's shared comment (design.md, decision 4).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.recurring import (
    RecurringSchedule,
    RecurringScheduleRepo,
    RecurringSlot,
    RecurringSlotOverride,
    RecurringSlotOverrideRepo,
    RecurringSlotRepo,
)
from src.domain.schedule import (
    next_weekday_on_or_after,
    series_occurrences,
    today_in_tz,
    utc_to_wall,
    wall_to_utc,
)

logger = logging.getLogger(__name__)

# A weekly slot is infinite; bound the search for its single nearest occurrence.
# A year of slack absorbs any realistic run of consecutive skips.
_NEXT_OCCURRENCE_HORIZON = timedelta(days=366)


@dataclass(slots=True)
class SeriesContext:
    """Active slots of a specialist plus their schedules and overrides.

    Loaded once per read and threaded into the appointment-read services so they
    can merge virtual future occurrences with real rows without re-querying. Only
    slots of active schedules are kept (filtered at load), so every slot here has a
    schedule in `schedules`.
    """

    slots: list[RecurringSlot]
    schedules: dict[int, RecurringSchedule]  # schedule_id → schedule
    overrides: dict[int, list[RecurringSlotOverride]]  # slot_id → its overrides
    today: date

    def schedule_for(self, slot: RecurringSlot) -> RecurringSchedule:
        return self.schedules[slot.schedule_id]

    def for_slot(self, slot_id: int | None) -> list[RecurringSlotOverride]:
        # No None keys exist, so a virtual slot_id=None simply maps to [].
        return self.overrides.get(slot_id, [])  # type: ignore[arg-type]


def _effective_comment(
    override: RecurringSlotOverride | None, schedule: RecurringSchedule
) -> str | None:
    """The occurrence's comment: its own override comment, else the schedule's."""
    if override is not None and override.comment is not None:
        return override.comment
    return schedule.comment


def _occurrence(  # noqa: PLR0913, PLR0917
    slot: RecurringSlot,
    schedule: RecurringSchedule,
    origin_date: date,
    starts_at: datetime,
    stamp: datetime,
    comment: str | None,
    *,
    mark: bool = False,
) -> Appointment:
    """An `Appointment` for one slot date — virtual (id=None) until settle saves it.

    `mark` flags a plain (non-moved) future occurrence so lists show the 🔁 marker.
    """
    return Appointment(
        id=None,
        specialist_id=schedule.specialist_id,
        client_id=schedule.client_id,
        starts_at=starts_at,
        comment=comment,
        created_at=stamp,
        updated_at=stamp,
        slot_id=slot.id,
        origin_date=origin_date,
        recurring_mark=mark,
    )


def _effective_starts_at(
    slot: RecurringSlot,
    day: date,
    override: RecurringSlotOverride | None,
    tz: str,
) -> datetime | None:
    """UTC instant of the occurrence on `day`, or None when the date is skipped."""
    if override is not None:
        if override.skipped:
            return None
        if override.moved_to is not None:
            return override.moved_to
    return wall_to_utc(day, slot.time_hhmm, tz)


def occurrences_landing_in(  # noqa: PLR0913, PLR0917
    slot: RecurringSlot,
    schedule: RecurringSchedule,
    overrides: list[RecurringSlotOverride],
    win_start: date,
    win_end: date,
    tz: str,
    today: date,
) -> list[Appointment]:
    """Occurrences whose effective instant's wall-date is in `[win_start, win_end)`.

    Keyed by where the occurrence actually lands: a plain date lands on itself, a
    moved date lands on its new instant (so it shows on that day and frees its
    original slot). A comment-only override leaves the grid date in place with the
    overridden comment. Past origins (< today) live in real rows and are excluded.
    Inactive slots or schedules contribute nothing.
    """
    if not slot.active or not schedule.active:
        return []
    ov_by_date = {o.original_date: o for o in overrides}
    result: list[Appointment] = []
    lower = max(win_start, today)
    for day in series_occurrences(slot.start_date, slot.weekday, lower, win_end):
        ov = ov_by_date.get(day)
        if ov is not None and (ov.skipped or ov.moved_to is not None):
            continue  # skipped, or moved away — a move is re-added below by landing
        starts_at = wall_to_utc(day, slot.time_hhmm, tz)
        comment = _effective_comment(ov, schedule)
        result.append(
            _occurrence(slot, schedule, day, starts_at, starts_at, comment, mark=True)
        )
    for ov in overrides:
        if ov.skipped or ov.moved_to is None or ov.original_date < today:
            continue
        if win_start <= utc_to_wall(ov.moved_to, tz).date() < win_end:
            comment = _effective_comment(ov, schedule)
            result.append(
                _occurrence(
                    slot, schedule, ov.original_date, ov.moved_to, ov.moved_to, comment
                )
            )
    return result


def occurrences_in_window(  # noqa: PLR0913, PLR0917
    schedule: RecurringSchedule,
    slots: list[RecurringSlot],
    overrides: dict[int, list[RecurringSlotOverride]],
    win_start: date,
    win_end: date,
    tz: str,
    today: date,
) -> list[Appointment]:
    """All occurrences of one schedule's slots in `[win_start, win_end)`, time-sorted.

    Aggregates every active slot of the schedule (two slots in one day land
    independently) for the schedule card's rolling 14-day list.
    """
    result: list[Appointment] = []
    for slot in slots:
        result.extend(
            occurrences_landing_in(
                slot,
                schedule,
                overrides.get(slot.id, []),  # type: ignore[arg-type]
                win_start,
                win_end,
                tz,
                today,
            )
        )
    return sorted(result, key=lambda occ: occ.starts_at)


def slot_taken_times(  # noqa: PLR0913, PLR0917
    slot: RecurringSlot,
    schedule: RecurringSchedule,
    overrides: list[RecurringSlotOverride],
    day: date,
    tz: str,
    today: date,
    *,
    exclude_slot_id: int | None = None,
) -> set[str]:
    """Wall-clock `HH:MM` slots occupied by this slot on `day` (future days only).

    A date moved to another day frees its original slot here and occupies the slot
    on the day it lands (occupancy reflects this date's skip/move). `exclude_slot_id`
    drops the slot being edited so its own current time is not flagged as taken.
    """
    if exclude_slot_id is not None and slot.id == exclude_slot_id:
        return set()
    return {
        f"{utc_to_wall(occ.starts_at, tz):%H:%M}"
        for occ in occurrences_landing_in(
            slot, schedule, overrides, day, day + timedelta(days=1), tz, today
        )
    }


def next_occurrence(
    slot: RecurringSlot,
    schedule: RecurringSchedule,
    overrides: list[RecurringSlotOverride],
    tz: str,
    today: date,
) -> Appointment | None:
    """The single nearest future occurrence of `slot` at or after `today`.

    Keyed by the instant it lands on, so a moved date competes by its new time.
    """
    occurrences = occurrences_landing_in(
        slot, schedule, overrides, today, today + _NEXT_OCCURRENCE_HORIZON, tz, today
    )
    return min(occurrences, key=lambda occ: occ.starts_at) if occurrences else None


def nearest_slot_landing_day(
    series_ctx: SeriesContext, day: date, tz: str, *, forward: bool
) -> date | None:
    """Calendar day the nearest future slot occurrence lands on, past `day`.

    Forward: the minimum landing date strictly after `day`, bounded by the same
    horizon as `next_occurrence`. Backward: always None — past occurrences are
    materialised into real rows, so active slots contribute no day before `today`.
    """
    if not forward:
        return None
    win_start = day + timedelta(days=1)
    win_end = win_start + _NEXT_OCCURRENCE_HORIZON
    landing_days = [
        utc_to_wall(occ.starts_at, tz).date()
        for slot in series_ctx.slots
        for occ in occurrences_landing_in(
            slot,
            series_ctx.schedule_for(slot),
            series_ctx.for_slot(slot.id),
            win_start,
            win_end,
            tz,
            series_ctx.today,
        )
    ]
    return min(landing_days) if landing_days else None


def _overrides_by_slot(
    overrides: list[RecurringSlotOverride],
) -> dict[int, list[RecurringSlotOverride]]:
    grouped: dict[int, list[RecurringSlotOverride]] = {}
    for ov in overrides:
        grouped.setdefault(ov.slot_id, []).append(ov)
    return grouped


async def load_series_context(  # noqa: PLR0913
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    *,
    specialist_id: int,
    now: datetime,
    tz: str,
) -> SeriesContext:
    """Load active schedules, their active slots, and overrides for read-merging."""
    schedules = {
        s.id: s
        for s in await schedule_repo.list_active_for_specialist(specialist_id)
        if s.id is not None
    }
    slots: list[RecurringSlot] = []
    for schedule_id in schedules:
        slots.extend(await slot_repo.list_for_schedule(schedule_id))
    overrides = _overrides_by_slot(
        await override_repo.list_for_specialist(specialist_id)
    )
    return SeriesContext(
        slots=slots,
        schedules=schedules,
        overrides=overrides,
        today=today_in_tz(now, tz),
    )


# --- use-cases: schedule and slots ------------------------------------------


async def create_schedule(
    schedule_repo: RecurringScheduleRepo,
    *,
    specialist_id: int,
    client_id: int,
    comment: str | None,
    now: datetime,
) -> RecurringSchedule:
    schedule = RecurringSchedule(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        comment=comment,
        active=True,
        created_at=now,
        updated_at=now,
    )
    saved = await schedule_repo.add(schedule)
    logger.info(
        "recurring.schedule_created",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "schedule_id": saved.id,
        },
    )
    return saved


async def add_slot(  # noqa: PLR0913
    slot_repo: RecurringSlotRepo,
    *,
    schedule_id: int,
    weekday: int,
    time_hhmm: str,
    tz: str,
    now: datetime,
    start_date: date | None = None,
) -> RecurringSlot:
    # start_date anchors the weekly grid at the nearest matching weekday ≥ today.
    if start_date is None:
        start_date = next_weekday_on_or_after(today_in_tz(now, tz), weekday)
    slot = RecurringSlot(
        id=None,
        schedule_id=schedule_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        active=True,
        start_date=start_date,
        # Nothing before start_date to freeze, so the grid is materialised up to it.
        materialized_through=start_date,
        created_at=now,
        updated_at=now,
    )
    saved = await slot_repo.add(slot)
    logger.info(
        "recurring.slot_added",
        extra={"schedule_id": schedule_id, "slot_id": saved.id},
    )
    return saved


async def edit_slot(  # noqa: PLR0913
    slot_repo: RecurringSlotRepo,
    *,
    slot_id: int,
    specialist_id: int,
    weekday: int,
    time_hhmm: str,
    now: datetime,
    tz: str,
) -> RecurringSlot | None:
    """Update one slot's rule; only future repeats change, past rows stay frozen.

    Changing the weekday shifts the whole grid, so `start_date` is recomputed to the
    nearest new weekday ≥ today and `materialized_through` is bumped to today — past
    rows of the old grid remain history. Time-only edits leave the grid untouched.
    """
    slot = await slot_repo.get_for_specialist(slot_id, specialist_id)
    if slot is None:
        return None
    today = today_in_tz(now, tz)
    if weekday != slot.weekday:
        start_date = next_weekday_on_or_after(today, weekday)
        materialized_through = today
    else:
        start_date = slot.start_date
        materialized_through = slot.materialized_through
    updated = await slot_repo.update_rule(
        slot_id,
        specialist_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        start_date=start_date,
        materialized_through=materialized_through,
        updated_at=now,
    )
    logger.info(
        "recurring.slot_edited",
        extra={"specialist_id": specialist_id, "slot_id": slot_id},
    )
    return updated


async def remove_slot(
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    *,
    slot_id: int,
    specialist_id: int,
    now: datetime,
) -> RecurringSlot | None:
    """Deactivate a slot; if it was the last active one, stop the whole schedule."""
    slot = await slot_repo.set_active(
        slot_id, specialist_id, active=False, updated_at=now
    )
    if slot is None:
        return None
    logger.info(
        "recurring.slot_removed",
        extra={"specialist_id": specialist_id, "slot_id": slot_id},
    )
    remaining = await slot_repo.list_for_schedule(slot.schedule_id)
    if not remaining:
        await schedule_repo.set_active(
            slot.schedule_id, specialist_id, active=False, updated_at=now
        )
        logger.info(
            "recurring.schedule_stopped",
            extra={"specialist_id": specialist_id, "schedule_id": slot.schedule_id},
        )
    return slot


async def stop_schedule(
    schedule_repo: RecurringScheduleRepo,
    *,
    schedule_id: int,
    specialist_id: int,
    now: datetime,
) -> RecurringSchedule | None:
    stopped = await schedule_repo.set_active(
        schedule_id, specialist_id, active=False, updated_at=now
    )
    if stopped is not None:
        logger.info(
            "recurring.schedule_stopped",
            extra={"specialist_id": specialist_id, "schedule_id": schedule_id},
        )
    return stopped


# --- use-cases: per-occurrence overrides ------------------------------------


def _find_override(
    overrides: list[RecurringSlotOverride], original_date: date
) -> RecurringSlotOverride | None:
    return next((o for o in overrides if o.original_date == original_date), None)


async def skip_occurrence(  # noqa: PLR0913
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    *,
    slot_id: int,
    specialist_id: int,
    original_date: date,
    now: datetime,
) -> RecurringSlotOverride | None:
    """Suppress a single date of the slot (skipped=true), preserving its comment."""
    if await slot_repo.get_for_specialist(slot_id, specialist_id) is None:
        return None
    existing = _find_override(await override_repo.list_for_slot(slot_id), original_date)
    ov = await override_repo.upsert(
        slot_id,
        original_date,
        skipped=True,
        moved_to=None,
        comment=existing.comment if existing is not None else None,
        created_at=now,
    )
    logger.info(
        "recurring.occurrence_skipped",
        extra={
            "specialist_id": specialist_id,
            "slot_id": slot_id,
            "original_date": original_date.isoformat(),
        },
    )
    return ov


async def move_occurrence(  # noqa: PLR0913
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    *,
    slot_id: int,
    specialist_id: int,
    original_date: date,
    moved_to: datetime,
    now: datetime,
) -> RecurringSlotOverride | None:
    """Move a single date of the slot to `moved_to` (UTC); un-skips, keeps comment."""
    if await slot_repo.get_for_specialist(slot_id, specialist_id) is None:
        return None
    existing = _find_override(await override_repo.list_for_slot(slot_id), original_date)
    ov = await override_repo.upsert(
        slot_id,
        original_date,
        skipped=False,
        moved_to=moved_to,
        comment=existing.comment if existing is not None else None,
        created_at=now,
    )
    logger.info(
        "recurring.occurrence_moved",
        extra={
            "specialist_id": specialist_id,
            "slot_id": slot_id,
            "original_date": original_date.isoformat(),
        },
    )
    return ov


async def set_occurrence_comment(  # noqa: PLR0913
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    *,
    slot_id: int,
    specialist_id: int,
    original_date: date,
    comment: str | None,
    now: datetime,
) -> RecurringSlotOverride | None:
    """Set a future occurrence's comment, preserving its skip/move state."""
    if await slot_repo.get_for_specialist(slot_id, specialist_id) is None:
        return None
    existing = _find_override(await override_repo.list_for_slot(slot_id), original_date)
    ov = await override_repo.upsert(
        slot_id,
        original_date,
        skipped=existing.skipped if existing is not None else False,
        moved_to=existing.moved_to if existing is not None else None,
        comment=comment,
        created_at=now,
    )
    logger.info(
        "recurring.occurrence_commented",
        extra={
            "specialist_id": specialist_id,
            "slot_id": slot_id,
            "original_date": original_date.isoformat(),
        },
    )
    return ov


async def set_schedule_comment(
    schedule_repo: RecurringScheduleRepo,
    *,
    schedule_id: int,
    specialist_id: int,
    comment: str | None,
    now: datetime,
) -> RecurringSchedule | None:
    """Set the series' shared default comment inherited by every occurrence."""
    schedule = await schedule_repo.set_comment(
        schedule_id, specialist_id, comment=comment, updated_at=now
    )
    if schedule is None:
        return None
    logger.info(
        "recurring.schedule_commented",
        extra={"specialist_id": specialist_id, "schedule_id": schedule_id},
    )
    return schedule


# --- materialisation --------------------------------------------------------


async def settle(  # noqa: PLR0913
    schedule_repo: RecurringScheduleRepo,
    slot_repo: RecurringSlotRepo,
    override_repo: RecurringSlotOverrideRepo,
    appt_repo: AppointmentsRepo,
    *,
    specialist_id: int,
    now: datetime,
    tz: str,
) -> None:
    """Freeze passed occurrences of active slots into real rows, idempotently.

    Materialises dates in `[materialized_through, today)` per slot, writing the
    effective comment into the row, then advances `materialized_through` to today.
    The per-slot guard plus insert-or-ignore make repeated/concurrent calls safe.
    """
    today = today_in_tz(now, tz)
    schedules = {
        s.id: s
        for s in await schedule_repo.list_active_for_specialist(specialist_id)
        if s.id is not None
    }
    if not schedules:
        return
    overrides = _overrides_by_slot(
        await override_repo.list_for_specialist(specialist_id)
    )
    for schedule_id, schedule in schedules.items():
        for slot in await slot_repo.list_for_schedule(schedule_id):
            assert slot.id is not None  # noqa: S101 — persisted slots have an id
            if slot.materialized_through >= today:
                continue
            await _settle_slot(
                appt_repo,
                slot,
                schedule,
                overrides.get(slot.id, []),  # type: ignore[arg-type]
                today,
                tz,
                now,
            )
            await slot_repo.set_materialized_through(
                slot.id, materialized_through=today
            )


async def _settle_slot(  # noqa: PLR0913, PLR0917
    appt_repo: AppointmentsRepo,
    slot: RecurringSlot,
    schedule: RecurringSchedule,
    overrides: list[RecurringSlotOverride],
    today: date,
    tz: str,
    now: datetime,
) -> None:
    ov_map = {o.original_date: o for o in overrides}
    dates = series_occurrences(
        slot.start_date, slot.weekday, slot.materialized_through, today
    )
    for day in dates:
        ov = ov_map.get(day)
        starts_at = _effective_starts_at(slot, day, ov, tz)
        if starts_at is None:
            continue  # skipped date leaves no history row
        comment = _effective_comment(ov, schedule)
        await appt_repo.insert_occurrence(
            _occurrence(slot, schedule, day, starts_at, now, comment)
        )
