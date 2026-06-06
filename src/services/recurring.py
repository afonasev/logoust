"""Use-cases for weekly recurring series: create, stop, expand, settle.

The split between materialised past and computed future lives here (design.md,
decision 1). `occurrences_landing_in` turns a rule into virtual `Appointment`s for
reading; `settle` freezes passed occurrences into real rows on specialist interaction.
A virtual occurrence is an `Appointment` with `id=None` and `series_id`/
`origin_date` set, so the schedule screens can merge it with real rows uniformly.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.recurring import (
    RecurringAppointment,
    RecurringException,
    RecurringExceptionsRepo,
    RecurringRepo,
)
from src.domain.schedule import (
    next_weekday_on_or_after,
    series_occurrences,
    today_in_tz,
    utc_to_wall,
    wall_to_utc,
)

logger = logging.getLogger(__name__)

# A weekly series is infinite; bound the search for its single nearest occurrence.
# A year of slack absorbs any realistic run of consecutive skips.
_NEXT_OCCURRENCE_HORIZON = timedelta(days=366)


@dataclass(slots=True)
class SeriesContext:
    """Active series of a specialist plus their exceptions, loaded once per read.

    Threaded into the appointment-read services so they can merge virtual future
    occurrences with real rows without each one re-querying the repositories.
    """

    series: list[RecurringAppointment]
    exceptions: dict[int, list[RecurringException]]  # series_id → its exceptions
    today: date

    def for_series(self, series_id: int | None) -> list[RecurringException]:
        # No None keys exist, so a virtual series_id=None simply maps to [].
        return self.exceptions.get(series_id, [])


def _occurrence(
    series: RecurringAppointment,
    origin_date: date,
    starts_at: datetime,
    stamp: datetime,
    *,
    mark: bool = False,
) -> Appointment:
    """An `Appointment` for one series date — virtual (id=None) until settle saves it.

    `mark` flags a plain (non-moved) future occurrence so lists show the 🔁 marker.
    """
    return Appointment(
        id=None,
        specialist_id=series.specialist_id,
        client_id=series.client_id,
        starts_at=starts_at,
        comment=series.comment,
        created_at=stamp,
        updated_at=stamp,
        series_id=series.id,
        origin_date=origin_date,
        recurring_mark=mark,
    )


def _effective_starts_at(
    series: RecurringAppointment,
    day: date,
    exception: RecurringException | None,
    tz: str,
) -> datetime | None:
    """UTC instant of the occurrence on `day`, or None when the date is skipped."""
    if exception is not None:
        return exception.new_starts_at  # None = skip; a value = moved instant
    return wall_to_utc(day, series.time_hhmm, tz)


async def create_series(  # noqa: PLR0913
    repo: RecurringRepo,
    *,
    specialist_id: int,
    client_id: int,
    weekday: int,
    time_hhmm: str,
    comment: str | None,
    tz: str,
    now: datetime,
    start_date: date | None = None,
) -> RecurringAppointment:
    # When created from a picked appointment date, that date is the first
    # occurrence; otherwise start at the nearest matching weekday ≥ today.
    if start_date is None:
        start_date = next_weekday_on_or_after(today_in_tz(now, tz), weekday)
    series = RecurringAppointment(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        comment=comment,
        active=True,
        start_date=start_date,
        # Nothing before start_date to freeze, so the grid is materialised up to it.
        materialized_through=start_date,
        created_at=now,
        updated_at=now,
    )
    saved = await repo.add(series)
    logger.info(
        "recurring.created",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "series_id": saved.id,
        },
    )
    return saved


async def stop_series(
    repo: RecurringRepo, *, series_id: int, specialist_id: int, now: datetime
) -> RecurringAppointment | None:
    stopped = await repo.set_active(
        series_id, specialist_id, active=False, updated_at=now
    )
    if stopped is not None:
        logger.info(
            "recurring.stopped",
            extra={"specialist_id": specialist_id, "series_id": series_id},
        )
    return stopped


async def edit_series(  # noqa: PLR0913
    repo: RecurringRepo,
    *,
    series_id: int,
    specialist_id: int,
    weekday: int,
    time_hhmm: str,
    comment: str | None,
    now: datetime,
    tz: str,
) -> RecurringAppointment | None:
    """Update a series rule; only future repeats change, past rows stay frozen.

    Changing the weekday shifts the whole grid, so `start_date` is recomputed to
    the nearest new weekday ≥ today and `materialized_through` is bumped to today —
    past rows of the old grid remain as history, the new rule applies going forward
    (design.md, decision 7). Time/comment-only edits leave the grid untouched.
    """
    series = await repo.get_for_specialist(series_id, specialist_id)
    if series is None:
        return None
    today = today_in_tz(now, tz)
    if weekday != series.weekday:
        start_date = next_weekday_on_or_after(today, weekday)
        materialized_through = today
    else:
        start_date = series.start_date
        materialized_through = series.materialized_through
    updated = await repo.update_rule(
        series_id,
        specialist_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        comment=comment,
        start_date=start_date,
        materialized_through=materialized_through,
        updated_at=now,
    )
    logger.info(
        "recurring.edited",
        extra={"specialist_id": specialist_id, "series_id": series_id},
    )
    return updated


async def skip_date(  # noqa: PLR0913
    repo: RecurringRepo,
    exc_repo: RecurringExceptionsRepo,
    *,
    series_id: int,
    specialist_id: int,
    original_date: date,
    now: datetime,
) -> RecurringException | None:
    """Suppress a single date of the series (new_starts_at IS NULL = skip)."""
    if await repo.get_for_specialist(series_id, specialist_id) is None:
        return None
    exc = await exc_repo.upsert(
        series_id, original_date, new_starts_at=None, created_at=now
    )
    logger.info(
        "recurring.date_skipped",
        extra={
            "specialist_id": specialist_id,
            "series_id": series_id,
            "original_date": original_date.isoformat(),
        },
    )
    return exc


async def move_date(  # noqa: PLR0913
    repo: RecurringRepo,
    exc_repo: RecurringExceptionsRepo,
    *,
    series_id: int,
    specialist_id: int,
    original_date: date,
    new_starts_at: datetime,
    now: datetime,
) -> RecurringException | None:
    """Move a single date of the series to `new_starts_at` (UTC)."""
    if await repo.get_for_specialist(series_id, specialist_id) is None:
        return None
    exc = await exc_repo.upsert(
        series_id, original_date, new_starts_at=new_starts_at, created_at=now
    )
    logger.info(
        "recurring.date_moved",
        extra={
            "specialist_id": specialist_id,
            "series_id": series_id,
            "original_date": original_date.isoformat(),
        },
    )
    return exc


def occurrences_landing_in(  # noqa: PLR0913, PLR0917
    series: RecurringAppointment,
    exceptions: list[RecurringException],
    win_start: date,
    win_end: date,
    tz: str,
    today: date,
) -> list[Appointment]:
    """Occurrences whose effective instant's wall-date is in `[win_start, win_end)`.

    Unlike `expand_future` (keyed by the planned grid date), this is keyed by where
    the occurrence actually lands: a plain date lands on itself, a moved date lands
    on its new instant (so a date moved to another day shows on that day and frees
    its original slot). Past origins (< today) live in real rows and are excluded.
    """
    if not series.active:
        return []
    exc_by_date = {e.original_date: e for e in exceptions}
    result: list[Appointment] = []
    lower = max(win_start, today)
    for day in series_occurrences(series.start_date, series.weekday, lower, win_end):
        if day in exc_by_date:
            continue  # skipped or moved-away — a move is re-added below by landing
        starts_at = wall_to_utc(day, series.time_hhmm, tz)
        result.append(_occurrence(series, day, starts_at, starts_at, mark=True))
    for exc in exceptions:
        if exc.new_starts_at is None or exc.original_date < today:
            continue
        if win_start <= utc_to_wall(exc.new_starts_at, tz).date() < win_end:
            result.append(
                _occurrence(
                    series, exc.original_date, exc.new_starts_at, exc.new_starts_at
                )
            )
    return result


def series_taken_times(
    series: RecurringAppointment,
    exceptions: list[RecurringException],
    day: date,
    tz: str,
    today: date,
) -> set[str]:
    """Wall-clock `HH:MM` slots occupied by this series on `day` (future days only).

    A date moved to another day frees its original slot here and occupies the slot
    on the day it lands (occupancy reflects this date's skip/move).
    """
    return {
        f"{utc_to_wall(occ.starts_at, tz):%H:%M}"
        for occ in occurrences_landing_in(
            series, exceptions, day, day + timedelta(days=1), tz, today
        )
    }


def next_occurrence(
    series: RecurringAppointment,
    exceptions: list[RecurringException],
    tz: str,
    today: date,
) -> Appointment | None:
    """The single nearest future occurrence of `series` at or after `today`.

    Keyed by the instant it lands on, so a moved date competes by its new time.
    """
    occurrences = occurrences_landing_in(
        series, exceptions, today, today + _NEXT_OCCURRENCE_HORIZON, tz, today
    )
    return min(occurrences, key=lambda occ: occ.starts_at) if occurrences else None


def nearest_series_landing_day(
    series_ctx: SeriesContext, day: date, tz: str, *, forward: bool
) -> date | None:
    """Calendar day the nearest future series occurrence lands on, past `day`.

    Forward: the minimum landing date strictly after `day`, bounded by the same
    horizon as `next_occurrence` so an infinite series cannot drive an unbounded
    scan. Backward: always None — past occurrences are materialised into real rows,
    so active series contribute no day before `today` (real rows cover the past).
    """
    if not forward:
        return None
    win_start = day + timedelta(days=1)
    win_end = win_start + _NEXT_OCCURRENCE_HORIZON
    landing_days = [
        utc_to_wall(occ.starts_at, tz).date()
        for s in series_ctx.series
        for occ in occurrences_landing_in(
            s, series_ctx.for_series(s.id), win_start, win_end, tz, series_ctx.today
        )
    ]
    return min(landing_days) if landing_days else None


def _exceptions_by_series(
    exceptions: list[RecurringException],
) -> dict[int, list[RecurringException]]:
    grouped: dict[int, list[RecurringException]] = {}
    for exc in exceptions:
        grouped.setdefault(exc.series_id, []).append(exc)
    return grouped


async def load_series_context(
    recurring_repo: RecurringRepo,
    exc_repo: RecurringExceptionsRepo,
    *,
    specialist_id: int,
    now: datetime,
    tz: str,
) -> SeriesContext:
    """Load active series and exceptions for merging into appointment reads."""
    series = await recurring_repo.list_active_for_specialist(specialist_id)
    exceptions = _exceptions_by_series(
        await exc_repo.list_for_specialist(specialist_id)
    )
    return SeriesContext(
        series=series, exceptions=exceptions, today=today_in_tz(now, tz)
    )


async def settle(  # noqa: PLR0913
    recurring_repo: RecurringRepo,
    exc_repo: RecurringExceptionsRepo,
    appt_repo: AppointmentsRepo,
    *,
    specialist_id: int,
    now: datetime,
    tz: str,
) -> None:
    """Freeze passed occurrences of active series into real rows, idempotently.

    Materialises dates in `[materialized_through, today)` per series, then advances
    `materialized_through` to today. The daily guard (skip series already settled
    today) plus insert-or-ignore make repeated and concurrent calls safe no-ops.
    """
    today = today_in_tz(now, tz)
    series_list = await recurring_repo.list_active_for_specialist(specialist_id)
    pending = [s for s in series_list if s.materialized_through < today]
    if not pending:
        return
    exc_by_series = _exceptions_by_series(
        await exc_repo.list_for_specialist(specialist_id)
    )
    for series in pending:
        await _settle_series(
            appt_repo, series, exc_by_series.get(series.id, []), today, tz, now
        )
        await recurring_repo.set_materialized_through(
            series.id, materialized_through=today
        )


async def _settle_series(  # noqa: PLR0913, PLR0917
    appt_repo: AppointmentsRepo,
    series: RecurringAppointment,
    exceptions: list[RecurringException],
    today: date,
    tz: str,
    now: datetime,
) -> None:
    exc_map = {e.original_date: e for e in exceptions}
    dates = series_occurrences(
        series.start_date, series.weekday, series.materialized_through, today
    )
    for day in dates:
        starts_at = _effective_starts_at(series, day, exc_map.get(day), tz)
        if starts_at is None:
            continue  # skipped date leaves no history row
        await appt_repo.insert_occurrence(_occurrence(series, day, starts_at, now))
