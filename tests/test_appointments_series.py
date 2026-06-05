"""Merge of virtual future occurrences into appointment-read services (section 5)."""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.recurring import RecurringAppointment
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.services.appointments import (
    create_appointment,
    list_client_future,
    list_free_windows,
    list_specialist_day,
    list_specialist_week,
    nearest_future_by_client,
    taken_slot_times,
)
from src.services.recurring import SeriesContext, load_series_context

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST
_SPECIALIST = 1
_CLIENT = 7
# 2026-06-15 is a Monday; 11:00 local.
_NOW = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
_TODAY = date(2026, 6, 15)


async def _make_series(
    session: AsyncSession, **overrides: object
) -> RecurringAppointment:
    base = {
        "id": None,
        "specialist_id": _SPECIALIST,
        "client_id": _CLIENT,
        "weekday": 0,  # Monday, == today
        "time_hhmm": "14:00",
        "comment": "регулярная",
        "active": True,
        "start_date": _TODAY,
        "materialized_through": _TODAY,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return await SqlAlchemyRecurringRepo(session).add(
        RecurringAppointment(**base)  # type: ignore[arg-type]
    )


async def _ctx(session: AsyncSession) -> SeriesContext:
    return await load_series_context(
        SqlAlchemyRecurringRepo(session),
        SqlAlchemyRecurringExceptionsRepo(session),
        specialist_id=_SPECIALIST,
        now=_NOW,
        tz=_TZ,
    )


def _specialist() -> Specialist:
    return Specialist(
        id=_SPECIALIST,
        invite_token="tok",
        telegram_chat_id=1,
        telegram_username=None,
        welcomed_at=_NOW,
        created_at=_NOW,
        timezone=_TZ,
        day_start="09:00",
        day_end="18:00",
        slot_minutes=60,
        working_days="0,1,2,3,4",
    )


# --- 5.1 occupancy ----------------------------------------------------------


async def test_repeat_marks_future_slot_taken(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    # 2026-06-22 is the next Monday → 14:00 occupied by the repeat.
    taken = await taken_slot_times(
        repo, specialist_id=_SPECIALIST, day=date(2026, 6, 22), tz=_TZ, series=ctx
    )
    assert "14:00" in taken


async def test_repeat_does_not_mark_other_weekday(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    # Tuesday is not the series weekday → nothing taken.
    taken = await taken_slot_times(
        repo, specialist_id=_SPECIALIST, day=date(2026, 6, 16), tz=_TZ, series=ctx
    )
    assert taken == set()


# --- 5.2 day / week ---------------------------------------------------------


async def test_repeat_shows_in_day(session: AsyncSession):
    series = await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    appts = await list_specialist_day(
        repo, specialist_id=_SPECIALIST, day=date(2026, 6, 22), tz=_TZ, series=ctx
    )
    assert len(appts) == 1
    assert appts[0].id is None
    assert appts[0].series_id == series.id
    assert appts[0].origin_date == date(2026, 6, 22)
    assert appts[0].starts_at == datetime(2026, 6, 22, 9, 0, tzinfo=UTC)


async def test_one_off_not_duplicated_in_day(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    # A real one-off on the same Monday at a different time coexists with the repeat.
    await create_appointment(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        day=date(2026, 6, 22),
        hhmm="16:00",
        comment=None,
        tz=_TZ,
        now=_NOW,
    )
    appts = await list_specialist_day(
        repo, specialist_id=_SPECIALIST, day=date(2026, 6, 22), tz=_TZ, series=ctx
    )
    # Exactly two: the one-off (16:00, real) and the repeat (14:00, virtual).
    assert len(appts) == 2
    times = sorted(f"{a.starts_at:%H:%M}" for a in appts)
    assert times == ["09:00", "11:00"]  # 14:00 and 16:00 local → 09:00, 11:00 UTC


async def test_week_without_series_returns_plain_rows(session: AsyncSession):
    # The series=None default path: no merging, just the real rows grouped.
    repo = SqlAlchemyAppointmentsRepo(session)
    groups = await list_specialist_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW
    )
    assert groups == []


async def test_repeat_shows_in_week(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    groups = await list_specialist_week(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, series=ctx
    )
    # Week is [today, today+7) → today's Monday repeat shows; next Monday is out.
    assert len(groups) == 1
    assert groups[0].day == _TODAY
    assert groups[0].appointments[0].series_id is not None


# --- 5.3 client card / nearest ----------------------------------------------


async def test_client_future_has_single_nearest_repeat(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    future = await list_client_future(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        tz=_TZ,
        now=_NOW,
        series=ctx,
    )
    # Exactly one occurrence (the nearest), not the infinite tail.
    assert len(future) == 1
    assert future[0].origin_date == _TODAY
    assert future[0].id is None


async def test_client_future_without_series_unchanged(session: AsyncSession):
    await _make_series(session, client_id=999)  # series for another client
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    future = await list_client_future(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        tz=_TZ,
        now=_NOW,
        series=ctx,
    )
    assert future == []


async def test_nearest_future_uses_repeat(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    nearest = await nearest_future_by_client(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, series=ctx
    )
    assert _CLIENT in nearest
    assert nearest[_CLIENT].origin_date == _TODAY


async def test_nearest_future_prefers_earlier_real_appointment(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    # A real appointment earlier today wins over the 14:00 repeat.
    await create_appointment(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        day=_TODAY,
        hhmm="09:00",
        comment=None,
        tz=_TZ,
        now=_NOW,
    )
    nearest = await nearest_future_by_client(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, series=ctx
    )
    assert nearest[_CLIENT].id is not None  # the real one, not the virtual repeat


# --- 5.5 availability -------------------------------------------------------


async def test_repeat_excludes_slot_from_free_windows(session: AsyncSession):
    await _make_series(session)
    ctx = await _ctx(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    windows = await list_free_windows(
        repo, specialist=_specialist(), now=_NOW, days=5, series=ctx
    )
    today_window = next(w for w in windows if w.day == _TODAY)
    # 14:00 is occupied by the active repeat, so it is not a free window.
    assert "14:00" not in today_window.free
    assert "15:00" in today_window.free


async def test_client_future_ignores_series_beyond_horizon(session: AsyncSession):
    # A series whose first date is beyond the one-year search horizon yields no
    # nearest occurrence, so the client card shows nothing for it.
    far = RecurringAppointment(
        id=None,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        weekday=0,
        time_hhmm="14:00",
        comment=None,
        active=True,
        start_date=date(2030, 1, 7),
        materialized_through=date(2030, 1, 7),
        created_at=_NOW,
        updated_at=_NOW,
    )
    ctx = SeriesContext(series=[far], exceptions={}, today=_TODAY)
    repo = SqlAlchemyAppointmentsRepo(session)
    future = await list_client_future(
        repo,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        tz=_TZ,
        now=_NOW,
        series=ctx,
    )
    assert future == []
    nearest = await nearest_future_by_client(
        repo, specialist_id=_SPECIALIST, tz=_TZ, now=_NOW, series=ctx
    )
    assert _CLIENT not in nearest
