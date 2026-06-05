from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.recurring import RecurringAppointment, RecurringException
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.services.recurring import (
    create_series,
    edit_series,
    move_date,
    next_occurrence,
    occurrences_landing_in,
    settle,
    skip_date,
    stop_series,
)

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST
_SPECIALIST = 1
_CLIENT = 7
# 2026-06-15 is a Monday; 11:00 local → today stays 2026-06-15 all day.
_NOW = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)


def _past_series(**overrides: object) -> RecurringAppointment:
    base = {
        "id": None,
        "specialist_id": _SPECIALIST,
        "client_id": _CLIENT,
        "weekday": 0,
        "time_hhmm": "14:00",
        "comment": "регулярная",
        "active": True,
        "start_date": date(2026, 6, 1),  # a Monday, two weeks before today
        "materialized_through": date(2026, 6, 1),
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return RecurringAppointment(**base)  # type: ignore[arg-type]


async def _list_appointments(session: AsyncSession) -> list:
    return await SqlAlchemyAppointmentsRepo(session).list_for_specialist_between(
        _SPECIALIST,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2027, 1, 1, tzinfo=UTC),
    )


# --- create -----------------------------------------------------------------


async def test_create_series_sets_start_to_next_weekday(session: AsyncSession):
    # today is Monday (weekday 0); asking for Wednesday (2) → 2026-06-17.
    series = await create_series(
        SqlAlchemyRecurringRepo(session),
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        weekday=2,
        time_hhmm="10:00",
        comment=None,
        tz=_TZ,
        now=_NOW,
    )
    assert series.start_date == date(2026, 6, 17)
    assert series.materialized_through == date(2026, 6, 17)
    assert series.active is True


async def test_create_series_today_when_weekday_matches(session: AsyncSession):
    series = await create_series(
        SqlAlchemyRecurringRepo(session),
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        weekday=0,  # Monday == today
        time_hhmm="14:00",
        comment="hi",
        tz=_TZ,
        now=_NOW,
    )
    assert series.start_date == date(2026, 6, 15)


# --- occurrences_landing_in ----------------------------------------------------------


def test_occurrences_landing_in_weekly_from_today():
    series = _past_series()
    occ = occurrences_landing_in(
        series, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, date(2026, 6, 15)
    )
    # Mondays at/after today, half-open end excludes 2026-07-13.
    assert [o.origin_date for o in occ] == [
        date(2026, 6, 15),
        date(2026, 6, 22),
        date(2026, 6, 29),
        date(2026, 7, 6),
    ]
    # 14:00 local (+05) → 09:00 UTC; virtual rows carry the series, no id.
    assert occ[0].starts_at == datetime(2026, 6, 15, 9, 0, tzinfo=UTC)
    assert occ[0].id is None
    assert occ[0].series_id == series.id
    assert occ[0].comment == "регулярная"


def test_occurrences_landing_in_excludes_past_dates():
    series = _past_series()
    # Range opens before today; only dates ≥ today come back.
    occ = occurrences_landing_in(
        series, [], date(2026, 6, 1), date(2026, 6, 23), _TZ, date(2026, 6, 15)
    )
    assert [o.origin_date for o in occ] == [date(2026, 6, 15), date(2026, 6, 22)]


def test_occurrences_landing_in_stopped_series_is_empty():
    series = _past_series(active=False)
    assert (
        occurrences_landing_in(
            series, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, date(2026, 6, 15)
        )
        == []
    )


def test_occurrences_landing_in_cross_day_move():
    series = _past_series()
    # Move the 2026-06-22 (Monday) occurrence to Wednesday 2026-06-24 16:00 local.
    moved = RecurringException(
        id=1,
        series_id=series.id or 0,
        original_date=date(2026, 6, 22),
        new_starts_at=datetime(2026, 6, 24, 11, 0, tzinfo=UTC),  # 16:00 +05
        created_at=_NOW,
    )
    # The new day shows it (keyed by landing), keyed to its original date.
    on_wed = occurrences_landing_in(
        series, [moved], date(2026, 6, 24), date(2026, 6, 25), _TZ, date(2026, 6, 15)
    )
    assert [o.origin_date for o in on_wed] == [date(2026, 6, 22)]
    assert on_wed[0].recurring_mark is False  # individualised → no 🔁
    # The original Monday no longer shows the occurrence (slot freed).
    on_mon = occurrences_landing_in(
        series, [moved], date(2026, 6, 22), date(2026, 6, 23), _TZ, date(2026, 6, 15)
    )
    assert on_mon == []


def test_occurrences_landing_in_ignores_moved_past_origin():
    series = _past_series()
    # An exception on a past date (already materialised) is not re-expanded.
    past_move = RecurringException(
        id=1,
        series_id=series.id or 0,
        original_date=date(2026, 6, 8),  # before today (2026-06-15)
        new_starts_at=datetime(2026, 6, 16, 9, 0, tzinfo=UTC),
        created_at=_NOW,
    )
    occ = occurrences_landing_in(
        series,
        [past_move],
        date(2026, 6, 15),
        date(2026, 6, 23),
        _TZ,
        date(2026, 6, 15),
    )
    # Only plain future Mondays; the past-origin move is ignored here.
    assert [o.origin_date for o in occ] == [date(2026, 6, 15), date(2026, 6, 22)]


# --- settle -----------------------------------------------------------------


async def _settle(session: AsyncSession) -> None:
    await settle(
        SqlAlchemyRecurringRepo(session),
        SqlAlchemyRecurringExceptionsRepo(session),
        SqlAlchemyAppointmentsRepo(session),
        specialist_id=_SPECIALIST,
        now=_NOW,
        tz=_TZ,
    )


async def test_settle_materializes_past_occurrences(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None

    await _settle(session)

    rows = await _list_appointments(session)
    # Past Mondays 06-01 and 06-08 frozen; today (06-15) stays virtual.
    assert sorted(r.origin_date for r in rows) == [date(2026, 6, 1), date(2026, 6, 8)]
    assert all(r.series_id == saved.id for r in rows)
    assert rows[0].starts_at == datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.materialized_through == date(2026, 6, 15)


async def test_settle_daily_guard_is_noop_on_repeat(session: AsyncSession):
    await SqlAlchemyRecurringRepo(session).add(_past_series())
    await _settle(session)
    await _settle(session)  # second interaction the same day
    rows = await _list_appointments(session)
    assert len(rows) == 2  # no duplicates


async def test_settle_insert_is_idempotent_under_replay(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    await _settle(session)
    # Simulate a concurrent/replayed settle: rewind the guard and run again.
    await repo.set_materialized_through(saved.id, materialized_through=date(2026, 6, 1))
    await _settle(session)
    rows = await _list_appointments(session)
    assert len(rows) == 2  # insert-or-ignore prevented duplicates


async def test_settle_without_active_series_is_noop(session: AsyncSession):
    await _settle(session)
    assert await _list_appointments(session) == []


# --- stop -------------------------------------------------------------------


async def test_stop_series_keeps_past_rows(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    await _settle(session)

    stopped = await stop_series(
        repo, series_id=saved.id, specialist_id=_SPECIALIST, now=_NOW
    )
    assert stopped is not None
    assert stopped.active is False
    # Materialised past survives the stop untouched.
    rows = await _list_appointments(session)
    assert len(rows) == 2
    # And the future is gone.
    assert (
        occurrences_landing_in(
            stopped, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, date(2026, 6, 15)
        )
        == []
    )


async def test_stop_series_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    assert (
        await stop_series(repo, series_id=saved.id, specialist_id=999, now=_NOW) is None
    )


# --- edit -------------------------------------------------------------------


async def test_edit_time_only_keeps_grid(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    edited = await edit_series(
        repo,
        series_id=saved.id,
        specialist_id=_SPECIALIST,
        weekday=0,  # unchanged
        time_hhmm="16:00",
        comment="новое",
        now=_NOW,
        tz=_TZ,
    )
    assert edited is not None
    assert edited.time_hhmm == "16:00"
    assert edited.comment == "новое"
    # Same weekday → grid anchors untouched.
    assert edited.start_date == saved.start_date
    assert edited.materialized_through == saved.materialized_through


async def test_edit_weekday_recomputes_start(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    edited = await edit_series(
        repo,
        series_id=saved.id,
        specialist_id=_SPECIALIST,
        weekday=2,  # Monday → Wednesday
        time_hhmm="14:00",
        comment=None,
        now=_NOW,
        tz=_TZ,
    )
    assert edited is not None
    # Nearest Wednesday ≥ today (2026-06-15 Mon) is 2026-06-17.
    assert edited.start_date == date(2026, 6, 17)
    assert edited.materialized_through == date(2026, 6, 15)


async def test_edit_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    assert (
        await edit_series(
            repo,
            series_id=saved.id,
            specialist_id=999,
            weekday=2,
            time_hhmm="14:00",
            comment=None,
            now=_NOW,
            tz=_TZ,
        )
        is None
    )


# --- skip / move ------------------------------------------------------------


async def test_skip_removes_only_that_date(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    await skip_date(
        repo,
        exc_repo,
        series_id=saved.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        now=_NOW,
    )
    exceptions = await exc_repo.list_for_series(saved.id)
    occ = occurrences_landing_in(
        saved, exceptions, date(2026, 6, 15), date(2026, 7, 6), _TZ, date(2026, 6, 15)
    )
    dates = [o.origin_date for o in occ]
    assert date(2026, 6, 22) not in dates  # skipped
    assert date(2026, 6, 15) in dates  # neighbour kept
    assert date(2026, 6, 29) in dates


async def test_skip_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    assert (
        await skip_date(
            repo,
            SqlAlchemyRecurringExceptionsRepo(session),
            series_id=saved.id,
            specialist_id=999,
            original_date=date(2026, 6, 22),
            now=_NOW,
        )
        is None
    )


async def test_move_shifts_only_that_date(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    new_at = datetime(2026, 6, 22, 11, 0, tzinfo=UTC)  # 16:00 local
    await move_date(
        repo,
        exc_repo,
        series_id=saved.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        new_starts_at=new_at,
        now=_NOW,
    )
    exceptions = await exc_repo.list_for_series(saved.id)
    occ = occurrences_landing_in(
        saved, exceptions, date(2026, 6, 15), date(2026, 7, 6), _TZ, date(2026, 6, 15)
    )
    moved = next(o for o in occ if o.origin_date == date(2026, 6, 22))
    assert moved.starts_at == new_at  # shown at the new time
    # Neighbours keep their planned 09:00 UTC time.
    other = next(o for o in occ if o.origin_date == date(2026, 6, 29))
    assert other.starts_at == datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


async def test_move_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    assert (
        await move_date(
            repo,
            SqlAlchemyRecurringExceptionsRepo(session),
            series_id=saved.id,
            specialist_id=999,
            original_date=date(2026, 6, 22),
            new_starts_at=datetime(2026, 6, 22, 11, 0, tzinfo=UTC),
            now=_NOW,
        )
        is None
    )


# --- settle honours exceptions ----------------------------------------------


async def test_settle_skips_skipped_past_date(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    # Skip the past 2026-06-08 occurrence before it materialises.
    await exc_repo.upsert(
        saved.id, date(2026, 6, 8), new_starts_at=None, created_at=_NOW
    )
    await _settle(session)
    rows = await _list_appointments(session)
    # Only 2026-06-01 is frozen; the skipped 06-08 leaves no row.
    assert [r.origin_date for r in rows] == [date(2026, 6, 1)]


async def test_settle_materializes_moved_past_date_at_new_time(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)
    saved = await repo.add(_past_series())
    assert saved.id is not None
    moved_at = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    await exc_repo.upsert(
        saved.id, date(2026, 6, 8), new_starts_at=moved_at, created_at=_NOW
    )
    await _settle(session)
    rows = await _list_appointments(session)
    moved = next(r for r in rows if r.origin_date == date(2026, 6, 8))
    assert moved.starts_at == moved_at


# --- next_occurrence edge ---------------------------------------------------


def test_next_occurrence_none_when_beyond_horizon():
    # A series starting beyond the one-year search horizon has no nearest date.
    far = RecurringAppointment(
        id=1,
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
    assert next_occurrence(far, [], _TZ, date(2026, 6, 15)) is None


def test_skip_via_expand_keeps_unrelated_exception_list():
    # occurrences_landing_in with an explicit skip exception drops just that date.
    series = _past_series()
    exc = RecurringException(
        id=1,
        series_id=series.id or 0,
        original_date=date(2026, 6, 15),
        new_starts_at=None,
        created_at=_NOW,
    )
    occ = occurrences_landing_in(
        series, [exc], date(2026, 6, 15), date(2026, 6, 30), _TZ, date(2026, 6, 15)
    )
    assert date(2026, 6, 15) not in [o.origin_date for o in occ]
    assert date(2026, 6, 22) in [o.origin_date for o in occ]
