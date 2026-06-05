from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.schedule import day_start_utc, today_in_tz, utc_to_wall, wall_to_utc

logger = logging.getLogger(__name__)


class PastDateError(Exception):
    """Raised when an appointment is created or moved to a day before today."""


@dataclass(slots=True)
class DayGroup:
    day: date
    appointments: list[Appointment]


@dataclass(slots=True)
class AppointmentsPage:
    appointments: list[Appointment]
    page: int
    has_prev: bool
    has_next: bool


def _ensure_not_past(day: date, tz: str, now: datetime) -> None:
    if day < today_in_tz(now, tz):
        raise PastDateError


async def taken_slot_times(
    repo: AppointmentsRepo,
    *,
    specialist_id: int,
    day: date,
    tz: str,
    exclude_id: int | None = None,
) -> set[str]:
    """Wall-clock `HH:MM` times the specialist already has booked on `day`.

    `exclude_id` drops the appointment being rescheduled so its own current slot
    is not flagged as taken.
    """
    start = day_start_utc(day, tz)
    end = day_start_utc(day + timedelta(days=1), tz)
    rows = await repo.list_for_specialist_between(specialist_id, start=start, end=end)
    return {
        f"{utc_to_wall(appt.starts_at, tz):%H:%M}"
        for appt in rows
        if appt.id != exclude_id
    }


def group_by_day(appointments: list[Appointment], tz: str) -> list[DayGroup]:
    """Group start-ascending appointments by their calendar day in `tz`."""
    groups: list[DayGroup] = []
    for appt in appointments:
        day = utc_to_wall(appt.starts_at, tz).date()
        if not groups or groups[-1].day != day:
            groups.append(DayGroup(day=day, appointments=[appt]))
        else:
            groups[-1].appointments.append(appt)
    return groups


async def create_appointment(  # noqa: PLR0913
    repo: AppointmentsRepo,
    *,
    specialist_id: int,
    client_id: int,
    day: date,
    hhmm: str,
    comment: str | None,
    tz: str,
    now: datetime,
) -> Appointment:
    _ensure_not_past(day, tz, now)
    starts_at = wall_to_utc(day, hhmm, tz)
    moment = datetime.now(UTC)
    appointment = Appointment(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        starts_at=starts_at,
        comment=comment,
        created_at=moment,
        updated_at=moment,
    )
    saved = await repo.add(appointment)
    logger.info(
        "appointment.created",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "appointment_id": saved.id,
        },
    )
    return saved


async def reschedule_appointment(  # noqa: PLR0913
    repo: AppointmentsRepo,
    *,
    appointment_id: int,
    specialist_id: int,
    day: date,
    hhmm: str,
    tz: str,
    now: datetime,
) -> Appointment | None:
    _ensure_not_past(day, tz, now)
    starts_at = wall_to_utc(day, hhmm, tz)
    moved = await repo.update_starts_at(
        appointment_id,
        specialist_id,
        starts_at=starts_at,
        updated_at=datetime.now(UTC),
    )
    if moved is None:
        return None
    logger.info(
        "appointment.rescheduled",
        extra={"specialist_id": specialist_id, "appointment_id": appointment_id},
    )
    return moved


async def delete_appointment(
    repo: AppointmentsRepo, *, appointment_id: int, specialist_id: int
) -> bool:
    deleted = await repo.delete(appointment_id, specialist_id)
    if deleted:
        logger.info(
            "appointment.deleted",
            extra={"specialist_id": specialist_id, "appointment_id": appointment_id},
        )
    return deleted


async def list_specialist_future_grouped(
    repo: AppointmentsRepo, *, specialist_id: int, tz: str, now: datetime
) -> list[DayGroup]:
    boundary = day_start_utc(today_in_tz(now, tz), tz)
    rows = await repo.list_future_for_specialist(specialist_id, since=boundary)
    return group_by_day(rows, tz)


async def list_specialist_day(
    repo: AppointmentsRepo, *, specialist_id: int, day: date, tz: str
) -> list[Appointment]:
    """Appointments of the specialist on a single calendar day in `tz`."""
    start = day_start_utc(day, tz)
    end = day_start_utc(day + timedelta(days=1), tz)
    return await repo.list_for_specialist_between(specialist_id, start=start, end=end)


async def list_specialist_week(
    repo: AppointmentsRepo, *, specialist_id: int, tz: str, now: datetime
) -> list[DayGroup]:
    """Appointments from today through the next six days, grouped by day."""
    today = today_in_tz(now, tz)
    start = day_start_utc(today, tz)
    end = day_start_utc(today + timedelta(days=7), tz)
    rows = await repo.list_for_specialist_between(specialist_id, start=start, end=end)
    return group_by_day(rows, tz)


@dataclass(slots=True)
class HistoryWeek:
    appointments: list[Appointment]  # descending by time
    week: int  # 0 = the 7 days before today; higher = older
    has_newer: bool
    has_older: bool


def history_week_monday(today: date, week: int) -> date:
    """Monday of the calendar week `week` weeks before the week containing `today`."""
    monday_this = today - timedelta(days=today.weekday())
    return monday_this - timedelta(days=7 * week)


async def list_specialist_history_week(
    repo: AppointmentsRepo, *, specialist_id: int, tz: str, now: datetime, week: int
) -> HistoryWeek:
    """History of a calendar week (Mon-Sun), `week` weeks back from this week.

    The current week (week 0) is capped at today: today's and future appointments
    belong to the day/feed view, not history.
    """
    today = today_in_tz(now, tz)
    monday = history_week_monday(today, week)
    start = day_start_utc(monday, tz)
    end = min(day_start_utc(monday + timedelta(days=7), tz), day_start_utc(today, tz))
    rows = await repo.list_for_specialist_between(specialist_id, start=start, end=end)
    rows.reverse()  # most recent first, like the rest of the history
    older = await repo.list_past_for_specialist(
        specialist_id, before=start, limit=1, offset=0
    )
    return HistoryWeek(
        appointments=rows, week=week, has_newer=week > 0, has_older=len(older) > 0
    )


async def nearest_future_by_client(
    repo: AppointmentsRepo, *, specialist_id: int, tz: str, now: datetime
) -> dict[int, Appointment]:
    """Map client_id → that client's earliest upcoming appointment (if any)."""
    boundary = day_start_utc(today_in_tz(now, tz), tz)
    rows = await repo.list_future_for_specialist(specialist_id, since=boundary)
    nearest: dict[int, Appointment] = {}
    for appt in rows:  # rows are ascending, so the first per client is the nearest
        nearest.setdefault(appt.client_id, appt)
    return nearest


async def list_client_future(
    repo: AppointmentsRepo,
    *,
    specialist_id: int,
    client_id: int,
    tz: str,
    now: datetime,
) -> list[Appointment]:
    boundary = day_start_utc(today_in_tz(now, tz), tz)
    return await repo.list_future_for_client(specialist_id, client_id, since=boundary)


async def list_client_history_page(  # noqa: PLR0913
    repo: AppointmentsRepo,
    *,
    specialist_id: int,
    client_id: int,
    tz: str,
    now: datetime,
    page: int,
    page_size: int,
) -> AppointmentsPage:
    boundary = day_start_utc(today_in_tz(now, tz), tz)
    rows = await repo.list_past_for_client(
        specialist_id,
        client_id,
        before=boundary,
        limit=page_size + 1,
        offset=page * page_size,
    )
    return _to_page(rows, page=page, page_size=page_size)


def _to_page(rows: list[Appointment], *, page: int, page_size: int) -> AppointmentsPage:
    # Fetch one extra row to detect a next page without a separate COUNT query.
    return AppointmentsPage(
        appointments=rows[:page_size],
        page=page,
        has_prev=page > 0,
        has_next=len(rows) > page_size,
    )
