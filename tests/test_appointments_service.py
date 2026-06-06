from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appointment import Appointment
from src.domain.recurring import (
    RecurringSchedule,
    RecurringSlot,
    RecurringSlotOverride,
)
from src.domain.schedule import utc_to_wall, wall_to_utc
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.services.appointments import (
    PastDateError,
    adjacent_shown_day,
    create_appointment,
    delete_appointment,
    group_by_day,
    list_client_future,
    list_client_history_page,
    list_free_windows,
    list_specialist_future_grouped,
    list_specialist_history_week,
    nearest_future_by_client,
    reschedule_appointment,
    schedule_landing_day,
    taken_slot_times,
    update_appointment_comment,
)
from src.services.recurring import SeriesContext

_TZ = "Asia/Yekaterinburg"  # UTC+5
_NOW = datetime(2026, 6, 4, 6, 0, tzinfo=UTC)  # 11:00 local, today = 2026-06-04
_SPECIALIST = 1
_CLIENT = 7


async def _create(
    session: AsyncSession,
    *,
    day: date,
    hhmm: str = "14:00",
    comment: str | None = None,
    client_id: int = _CLIENT,
):
    return await create_appointment(
        SqlAlchemyAppointmentsRepo(session),
        specialist_id=_SPECIALIST,
        client_id=client_id,
        day=day,
        hhmm=hhmm,
        comment=comment,
        tz=_TZ,
        now=_NOW,
    )


async def test_create_stores_utc_from_wall_time(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 4), hhmm="14:00")
    # 14:00 local (+05) → 09:00 UTC.
    assert appt.starts_at == datetime(2026, 6, 4, 9, 0, tzinfo=UTC)
    assert appt.comment is None


async def test_create_with_comment(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 4), comment="принести тетрадь")
    assert appt.comment == "принести тетрадь"


async def test_create_today_is_allowed(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 4))
    assert appt.id is not None


async def test_create_rejects_past_date(session: AsyncSession):
    with pytest.raises(PastDateError):
        await _create(session, day=date(2026, 6, 3))


async def test_reschedule_updates_time_keeps_comment(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 5), comment="держим")
    assert appt.id is not None
    moved = await reschedule_appointment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=appt.id,
        specialist_id=_SPECIALIST,
        day=date(2026, 6, 6),
        hhmm="10:00",
        tz=_TZ,
        now=_NOW,
    )
    assert moved is not None
    assert moved.starts_at == datetime(2026, 6, 6, 5, 0, tzinfo=UTC)
    assert moved.comment == "держим"


async def test_reschedule_rejects_past_date(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 5))
    assert appt.id is not None
    with pytest.raises(PastDateError):
        await reschedule_appointment(
            SqlAlchemyAppointmentsRepo(session),
            appointment_id=appt.id,
            specialist_id=_SPECIALIST,
            day=date(2026, 6, 1),
            hhmm="10:00",
            tz=_TZ,
            now=_NOW,
        )


async def test_reschedule_missing_returns_none(session: AsyncSession):
    moved = await reschedule_appointment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=999,
        specialist_id=_SPECIALIST,
        day=date(2026, 6, 6),
        hhmm="10:00",
        tz=_TZ,
        now=_NOW,
    )
    assert moved is None


async def test_update_comment_sets_and_clears(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 5), comment="старый")
    assert appt.id is not None
    updated = await update_appointment_comment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=appt.id,
        specialist_id=_SPECIALIST,
        comment="новый",
        now=_NOW,
    )
    assert updated is not None
    assert updated.comment == "новый"
    cleared = await update_appointment_comment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=appt.id,
        specialist_id=_SPECIALIST,
        comment=None,
        now=_NOW,
    )
    assert cleared is not None
    assert cleared.comment is None


async def test_update_comment_missing_returns_none(session: AsyncSession):
    result = await update_appointment_comment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=999,
        specialist_id=_SPECIALIST,
        comment="x",
        now=_NOW,
    )
    assert result is None


async def test_delete_removes_and_reports(session: AsyncSession):
    appt = await _create(session, day=date(2026, 6, 5))
    assert appt.id is not None
    assert await delete_appointment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=appt.id,
        specialist_id=_SPECIALIST,
    )
    assert not await delete_appointment(
        SqlAlchemyAppointmentsRepo(session),
        appointment_id=appt.id,
        specialist_id=_SPECIALIST,
    )


async def test_specialist_future_grouped_by_day(session: AsyncSession):
    await _create(session, day=date(2026, 6, 5), hhmm="14:00")
    await _create(session, day=date(2026, 6, 5), hhmm="10:00")
    await _create(session, day=date(2026, 6, 6), hhmm="09:00")
    groups = await list_specialist_future_grouped(
        SqlAlchemyAppointmentsRepo(session),
        specialist_id=_SPECIALIST,
        tz=_TZ,
        now=_NOW,
    )
    assert [g.day for g in groups] == [date(2026, 6, 5), date(2026, 6, 6)]
    # Within a day appointments stay start-ascending.
    first_day_times = [a.starts_at for a in groups[0].appointments]
    assert first_day_times == sorted(first_day_times)
    assert len(groups[0].appointments) == 2


async def test_today_appointment_stays_in_future(session: AsyncSession):
    # 09:00 local today is already past relative to _NOW (11:00) but the day is today.
    await _create(session, day=date(2026, 6, 4), hhmm="09:00")
    groups = await list_specialist_future_grouped(
        SqlAlchemyAppointmentsRepo(session),
        specialist_id=_SPECIALIST,
        tz=_TZ,
        now=_NOW,
    )
    assert groups[0].day == date(2026, 6, 4)


async def _seed_past(session: AsyncSession, day: date) -> None:
    # History appointments are in the past, so insert directly (create_appointment
    # forbids past dates).
    now = datetime.now(UTC)
    await SqlAlchemyAppointmentsRepo(session).add(
        Appointment(
            id=None,
            specialist_id=_SPECIALIST,
            client_id=_CLIENT,
            starts_at=wall_to_utc(day, "14:00", _TZ),
            comment=None,
            created_at=now,
            updated_at=now,
        )
    )


async def test_specialist_history_by_calendar_week(session: AsyncSession):
    # _NOW → today = 2026-06-04 (Thu). Calendar weeks (Mon-Sun):
    # week 0 = [06-01, 06-04) capped at today, week 1 = [05-25, 06-01).
    repo = SqlAlchemyAppointmentsRepo(session)
    await _seed_past(session, date(2026, 6, 2))  # this week (Tue), past
    await _seed_past(session, date(2026, 5, 27))  # previous week (Wed)
    await _seed_past(session, date(2026, 5, 1))  # older still

    w0 = await list_specialist_history_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, week=0
    )
    assert [utc_to_wall(a.starts_at, _TZ).date() for a in w0.appointments] == [
        date(2026, 6, 2)
    ]
    assert w0.has_newer is False  # current week
    assert w0.has_older is True

    w1 = await list_specialist_history_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, week=1
    )
    assert [utc_to_wall(a.starts_at, _TZ).date() for a in w1.appointments] == [
        date(2026, 5, 27)
    ]
    assert w1.has_newer is True
    assert w1.has_older is True

    w2 = await list_specialist_history_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, week=2
    )
    assert w2.appointments == []  # empty week, but older records exist
    assert w2.has_older is True


async def test_history_week_today_is_monday_has_no_past_days(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await _seed_past(session, date(2026, 5, 27))  # last week
    monday_now = datetime(2026, 6, 1, 6, 0, tzinfo=UTC)  # 2026-06-01 is Monday
    w0 = await list_specialist_history_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=monday_now, week=0
    )
    assert w0.appointments == []  # week just started, nothing past yet
    assert w0.has_older is True


async def test_client_future_and_history(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await _create(session, day=date(2026, 6, 6), client_id=_CLIENT)
    await create_appointment(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        day=date(2026, 5, 1),
        hhmm="14:00",
        comment=None,
        tz=_TZ,
        now=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
    )
    await _create(session, day=date(2026, 6, 6), client_id=99)

    future = await list_client_future(
        repo, specialist_id=_SPECIALIST, client_id=_CLIENT, tz=_TZ, now=_NOW
    )
    assert [a.client_id for a in future] == [_CLIENT]

    history = await list_client_history_page(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        tz=_TZ,
        now=_NOW,
        page=0,
        page_size=8,
    )
    assert [a.client_id for a in history.appointments] == [_CLIENT]


async def test_taken_slot_times_for_day(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await _create(session, day=date(2026, 6, 5), hhmm="14:00")
    await _create(session, day=date(2026, 6, 5), hhmm="10:00")
    await _create(session, day=date(2026, 6, 6), hhmm="09:00")  # other day excluded
    taken = await taken_slot_times(
        repo, specialist_id=_SPECIALIST, day=date(2026, 6, 5), tz=_TZ
    )
    assert taken == {"14:00", "10:00"}


async def test_taken_slot_times_excludes_given_appointment(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    appt = await _create(session, day=date(2026, 6, 5), hhmm="14:00")
    taken = await taken_slot_times(
        repo,
        specialist_id=_SPECIALIST,
        day=date(2026, 6, 5),
        tz=_TZ,
        exclude_id=appt.id,
    )
    assert taken == set()


async def test_nearest_future_by_client(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await _create(session, day=date(2026, 6, 6), hhmm="10:00", client_id=7)
    await _create(session, day=date(2026, 6, 5), hhmm="14:00", client_id=7)  # earlier
    await _create(session, day=date(2026, 6, 7), hhmm="09:00", client_id=9)
    nearest = await nearest_future_by_client(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW
    )
    assert utc_to_wall(nearest[7].starts_at, _TZ).date() == date(2026, 6, 5)
    assert utc_to_wall(nearest[9].starts_at, _TZ).date() == date(2026, 6, 7)


def test_group_by_day_empty():
    assert group_by_day([], _TZ) == []


def _specialist(
    *,
    working_days: str = "0,1,2,3,4",
    day_start: str = "09:00",
    day_end: str = "14:00",
    slot_minutes: int = 60,
) -> Specialist:
    return Specialist(
        id=_SPECIALIST,
        invite_token="t",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=_NOW,
        timezone=_TZ,
        day_start=day_start,
        day_end=day_end,
        slot_minutes=slot_minutes,
        working_days=working_days,
    )


async def test_free_windows_skips_weekend_and_hides_past_today(session: AsyncSession):
    # today = Thu 2026-06-04, now 11:00 local. Grid 09:00-14:00 hourly.
    windows = await list_free_windows(
        SqlAlchemyAppointmentsRepo(session), specialist=_specialist(), now=_NOW
    )
    assert [w.day for w in windows] == [
        date(2026, 6, 4),  # Thu (today)
        date(2026, 6, 5),  # Fri
        date(2026, 6, 8),  # Mon — Sat/Sun skipped
        date(2026, 6, 9),
        date(2026, 6, 10),
    ]
    # Today hides slots at/below 11:00 → only 12:00, 13:00 remain.
    assert windows[0].free == ["12:00", "13:00"]
    # A clear future day shows the whole grid, ascending.
    assert windows[1].free == ["09:00", "10:00", "11:00", "12:00", "13:00"]


async def test_free_windows_excludes_taken_slot(session: AsyncSession):
    await _create(session, day=date(2026, 6, 5), hhmm="10:00")
    windows = await list_free_windows(
        SqlAlchemyAppointmentsRepo(session), specialist=_specialist(), now=_NOW
    )
    friday = next(w for w in windows if w.day == date(2026, 6, 5))
    assert friday.free == ["09:00", "11:00", "12:00", "13:00"]


async def test_free_windows_day_fully_booked_stays_with_empty_list(
    session: AsyncSession,
):
    for hhmm in ("09:00", "10:00", "11:00", "12:00", "13:00"):
        await _create(session, day=date(2026, 6, 5), hhmm=hhmm)
    windows = await list_free_windows(
        SqlAlchemyAppointmentsRepo(session), specialist=_specialist(), now=_NOW
    )
    friday = next(w for w in windows if w.day == date(2026, 6, 5))
    assert friday.free == []
    assert len(windows) == 5  # the fully-booked day still counts toward the five


async def test_free_windows_empty_working_days_returns_empty(session: AsyncSession):
    windows = await list_free_windows(
        SqlAlchemyAppointmentsRepo(session),
        specialist=_specialist(working_days=""),
        now=_NOW,
    )
    assert windows == []


_WD = {0, 1, 2, 3, 4}  # Mon-Fri


async def test_adjacent_shown_day_forward_skips_empty_weekend(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 5),
        forward=True,  # Friday
    )
    assert nxt == date(2026, 6, 8)  # Monday; empty Sat/Sun skipped


async def test_adjacent_shown_day_backward_skips_empty_weekend(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    prev = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 8),
        forward=False,  # Monday
    )
    assert prev == date(2026, 6, 5)  # Friday


async def test_adjacent_shown_day_nonworking_with_appt_not_skipped(
    session: AsyncSession,
):
    await _create(session, day=date(2026, 6, 6), hhmm="14:00")  # Saturday appt
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 5),
        forward=True,
    )
    assert nxt == date(2026, 6, 6)  # the appointment day is nearer than Monday


async def test_adjacent_shown_day_empty_working_uses_appointments(
    session: AsyncSession,
):
    await _create(session, day=date(2026, 6, 10), hhmm="09:00")
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=set(),
        tz=_TZ,
        day=date(2026, 6, 4),
        forward=True,
    )
    assert nxt == date(2026, 6, 10)


async def test_adjacent_shown_day_none_when_nothing_ahead(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=set(),
        tz=_TZ,
        day=date(2026, 6, 4),
        forward=True,
    )
    assert nxt is None


async def test_adjacent_shown_day_backward_uses_past_appt(session: AsyncSession):
    await _seed_past(session, date(2026, 6, 2))  # Tuesday, past
    repo = SqlAlchemyAppointmentsRepo(session)
    prev = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=set(),
        tz=_TZ,
        day=date(2026, 6, 4),
        forward=False,
    )
    assert prev == date(2026, 6, 2)


async def test_landing_today_when_working(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    landing = await schedule_landing_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        today=date(2026, 6, 4),  # Thursday, a working day
    )
    assert landing == date(2026, 6, 4)


async def test_landing_skips_empty_nonworking_today(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    landing = await schedule_landing_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days={0, 1, 2},
        tz=_TZ,
        today=date(2026, 6, 4),  # Thursday is non-working here, no appts
    )
    assert landing == date(2026, 6, 8)  # jumps to next working day (Monday)


async def test_landing_today_when_nonworking_but_has_appt(session: AsyncSession):
    await _create(session, day=date(2026, 6, 4), hhmm="14:00")
    repo = SqlAlchemyAppointmentsRepo(session)
    landing = await schedule_landing_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days={0, 1, 2},
        tz=_TZ,
        today=date(2026, 6, 4),
    )
    assert landing == date(2026, 6, 4)  # today has an appointment → stays


async def test_landing_falls_back_to_today_when_nothing(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    landing = await schedule_landing_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=set(),
        tz=_TZ,
        today=date(2026, 6, 4),
    )
    assert landing == date(2026, 6, 4)


# --- series-aware navigation ------------------------------------------------


def _slot(
    weekday: int, start_date: date, *, slot_id: int = 1, time_hhmm: str = "14:00"
) -> RecurringSlot:
    return RecurringSlot(
        id=slot_id,
        schedule_id=1,
        weekday=weekday,
        time_hhmm=time_hhmm,
        active=True,
        start_date=start_date,
        materialized_through=start_date,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _schedule() -> RecurringSchedule:
    return RecurringSchedule(
        id=1,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        comment="регулярная",
        active=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _ctx(
    slots: list[RecurringSlot],
    *,
    overrides: list[RecurringSlotOverride] | None = None,
    today: date = date(2026, 6, 4),
) -> SeriesContext:
    grouped: dict[int, list[RecurringSlotOverride]] = {}
    for ov in overrides or []:
        grouped.setdefault(ov.slot_id, []).append(ov)
    return SeriesContext(
        slots=slots,
        schedules={1: _schedule()},
        overrides=grouped,
        today=today,
    )


async def test_adjacent_shown_day_forward_reaches_moved_occurrence(
    session: AsyncSession,
):
    # Tuesday slot; the 2026-06-09 repeat is moved to Sunday 2026-06-07 (no real
    # row). From Saturday, "next" must land on that Sunday, not skip to Monday.
    slot = _slot(weekday=1, start_date=date(2026, 6, 2))  # Tue
    moved = RecurringSlotOverride(
        id=1,
        slot_id=1,
        original_date=date(2026, 6, 9),
        skipped=False,
        moved_to=wall_to_utc(date(2026, 6, 7), "10:00", _TZ),  # Sun
        comment=None,
        created_at=_NOW,
    )
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 6),  # Saturday
        forward=True,
        series=_ctx([slot], overrides=[moved]),
    )
    assert nxt == date(2026, 6, 7)  # Sunday with the moved repeat, not Monday


async def test_adjacent_shown_day_forward_reaches_plain_nonworking_repeat(
    session: AsyncSession,
):
    # A plain (non-moved) Sunday slot whose weekday is outside working days.
    slot = _slot(weekday=6, start_date=date(2026, 6, 7))  # Sunday
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 6),  # Saturday
        forward=True,
        series=_ctx([slot]),
    )
    assert nxt == date(2026, 6, 7)  # the Sunday repeat, not Monday


async def test_adjacent_shown_day_empty_series_still_skips_weekend(
    session: AsyncSession,
):
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 5),  # Friday
        forward=True,
        series=_ctx([]),
    )
    assert nxt == date(2026, 6, 8)  # Monday; empty weekend still skipped


async def test_adjacent_shown_day_forward_two_slots_same_weekday(
    session: AsyncSession,
):
    # NEW: two slots landing on the same Sunday both point "next" at that Sunday
    # (one schedule, two slots). The nearer landing day wins over Monday.
    slots = [
        _slot(weekday=6, start_date=date(2026, 6, 7), slot_id=1, time_hhmm="10:00"),
        _slot(weekday=6, start_date=date(2026, 6, 7), slot_id=2, time_hhmm="15:00"),
    ]
    repo = SqlAlchemyAppointmentsRepo(session)
    nxt = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=_WD,
        tz=_TZ,
        day=date(2026, 6, 6),  # Saturday
        forward=True,
        series=_ctx(slots),
    )
    assert nxt == date(2026, 6, 7)  # the Sunday both slots land on


async def test_adjacent_shown_day_none_when_only_series_is_backward(
    session: AsyncSession,
):
    # Only a future slot repeat exists, no working days, no real rows. Backward
    # must not surface a virtual occurrence (the past lives in real rows only).
    slot = _slot(weekday=6, start_date=date(2026, 6, 7))  # Sunday, future
    repo = SqlAlchemyAppointmentsRepo(session)
    prev = await adjacent_shown_day(
        repo,
        specialist_id=_SPECIALIST,
        working_days=set(),
        tz=_TZ,
        day=date(2026, 6, 10),
        forward=False,
        series=_ctx([slot]),
    )
    assert prev is None
