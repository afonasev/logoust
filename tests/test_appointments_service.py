from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appointment import Appointment
from src.domain.schedule import utc_to_wall, wall_to_utc
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.services.appointments import (
    PastDateError,
    create_appointment,
    delete_appointment,
    group_by_day,
    list_client_future,
    list_client_history_page,
    list_specialist_future_grouped,
    list_specialist_history_week,
    nearest_future_by_client,
    reschedule_appointment,
    taken_slot_times,
)

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
