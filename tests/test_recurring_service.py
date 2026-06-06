"""Use-case + occurrence-math tests for the multi-slot recurring schedule service.

Model: a `RecurringSchedule` owns N weekly `RecurringSlot`s; each slot owns
per-date `RecurringSlotOverride`s (skip / move / comment, three independent axes).

Style mirrors the old single-rule tests: real SQLAlchemy repos from
`recurring_repo.py` against the `session` fixture. SQLite has FK enforcement off
by default, so we use bare specialist/client ids without seeding those tables —
the recurring tables under test never join out to them here.

Occurrence-math edge cases that the appointment-read services own (day/week
merge, free windows, nearest-future) live in `test_appointments_series.py`; this
file covers the recurring service's own surface and is careful not to duplicate.
"""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.recurring import (
    RecurringSchedule,
    RecurringSlot,
    RecurringSlotOverride,
)
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.services.recurring import (
    add_slot,
    create_schedule,
    edit_slot,
    load_series_context,
    move_occurrence,
    nearest_slot_landing_day,
    next_occurrence,
    occurrences_in_window,
    occurrences_landing_in,
    remove_slot,
    set_occurrence_comment,
    settle,
    skip_occurrence,
    slot_taken_times,
    stop_schedule,
)

_TZ = "Europe/Moscow"  # UTC+3, no DST since 2014
_SPECIALIST = 1
_OTHER_SPECIALIST = 999
_CLIENT = 7
# 2026-06-15 is a Monday; 06:00 UTC → 09:00 Moscow, so today is 2026-06-15.
_NOW = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
_TODAY = date(2026, 6, 15)
# 14:00 Moscow wall == 11:00 UTC.
_UTC_1400 = datetime(2026, 6, 15, 11, 0, tzinfo=UTC)


def _sched_repo(session: AsyncSession) -> SqlAlchemyRecurringScheduleRepo:
    return SqlAlchemyRecurringScheduleRepo(session)


def _slot_repo(session: AsyncSession) -> SqlAlchemyRecurringSlotRepo:
    return SqlAlchemyRecurringSlotRepo(session)


def _ov_repo(session: AsyncSession) -> SqlAlchemyRecurringSlotOverrideRepo:
    return SqlAlchemyRecurringSlotOverrideRepo(session)


def _appt_repo(session: AsyncSession) -> SqlAlchemyAppointmentsRepo:
    return SqlAlchemyAppointmentsRepo(session)


async def _schedule(
    session: AsyncSession,
    *,
    comment: str | None = "регулярная",
    client_id: int = _CLIENT,
) -> RecurringSchedule:
    return await create_schedule(
        _sched_repo(session),
        specialist_id=_SPECIALIST,
        client_id=client_id,
        comment=comment,
        now=_NOW,
    )


async def _add_slot(
    session: AsyncSession,
    schedule_id: int,
    *,
    weekday: int = 0,
    time_hhmm: str = "14:00",
    start_date: date | None = None,
) -> RecurringSlot:
    return await add_slot(
        _slot_repo(session),
        schedule_id=schedule_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        tz=_TZ,
        now=_NOW,
        start_date=start_date,
    )


# --- helper to build a plain in-memory slot for pure occurrence-math tests ----


def _slot(  # noqa: PLR0913
    *,
    slot_id: int = 1,
    schedule_id: int = 1,
    weekday: int = 0,
    time_hhmm: str = "14:00",
    active: bool = True,
    start_date: date = date(2026, 6, 1),  # a Monday, two weeks before today
    materialized_through: date = date(2026, 6, 1),
) -> RecurringSlot:
    return RecurringSlot(
        id=slot_id,
        schedule_id=schedule_id,
        weekday=weekday,
        time_hhmm=time_hhmm,
        active=active,
        start_date=start_date,
        materialized_through=materialized_through,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _schedule_obj(
    *, schedule_id: int = 1, comment: str | None = "регулярная", active: bool = True
) -> RecurringSchedule:
    return RecurringSchedule(
        id=schedule_id,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        comment=comment,
        active=active,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _override(
    *,
    slot_id: int = 1,
    original_date: date,
    skipped: bool = False,
    moved_to: datetime | None = None,
    comment: str | None = None,
) -> RecurringSlotOverride:
    return RecurringSlotOverride(
        id=1,
        slot_id=slot_id,
        original_date=original_date,
        skipped=skipped,
        moved_to=moved_to,
        comment=comment,
        created_at=_NOW,
    )


# ============================================================================
# use-case: create_schedule
# ============================================================================


async def test_create_schedule_persists_active(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    assert schedule.active is True
    assert schedule.comment == "регулярная"
    fetched = await _sched_repo(session).get_for_specialist(schedule.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.comment == "регулярная"


async def test_create_schedule_allows_none_comment(session: AsyncSession):
    schedule = await _schedule(session, comment=None)
    assert schedule.comment is None


# ============================================================================
# use-case: add_slot
# ============================================================================


async def test_add_slot_defaults_start_to_next_weekday(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    # today is Monday (0); asking for Wednesday (2) → 2026-06-17.
    slot = await _add_slot(session, schedule.id, weekday=2, time_hhmm="10:00")
    assert slot.start_date == date(2026, 6, 17)
    # Nothing to freeze before start → materialized_through anchors at start_date.
    assert slot.materialized_through == date(2026, 6, 17)
    assert slot.active is True
    assert slot.schedule_id == schedule.id


async def test_add_slot_default_start_today_when_weekday_matches(
    session: AsyncSession,
):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id, weekday=0)  # Monday == today
    assert slot.start_date == _TODAY


async def test_add_slot_explicit_start_date(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    explicit = date(2026, 6, 22)  # next Monday
    slot = await _add_slot(session, schedule.id, weekday=0, start_date=explicit)
    assert slot.start_date == explicit
    assert slot.materialized_through == explicit


# ============================================================================
# use-case: edit_slot
# ============================================================================


async def test_edit_slot_time_only_keeps_grid(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id, weekday=0, time_hhmm="14:00")
    assert slot.id is not None
    edited = await edit_slot(
        _slot_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        weekday=0,  # unchanged
        time_hhmm="16:00",
        now=_NOW,
        tz=_TZ,
    )
    assert edited is not None
    assert edited.time_hhmm == "16:00"
    # Same weekday → grid anchors untouched.
    assert edited.start_date == slot.start_date
    assert edited.materialized_through == slot.materialized_through


async def test_edit_slot_weekday_change_recomputes_grid(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id, weekday=0, time_hhmm="14:00")
    assert slot.id is not None
    edited = await edit_slot(
        _slot_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        weekday=2,  # Monday → Wednesday
        time_hhmm="14:00",
        now=_NOW,
        tz=_TZ,
    )
    assert edited is not None
    assert edited.weekday == 2
    # Nearest Wednesday ≥ today (2026-06-15 Mon) is 2026-06-17.
    assert edited.start_date == date(2026, 6, 17)
    # Grid reset → past frozen; materialized_through bumped to today.
    assert edited.materialized_through == _TODAY


async def test_edit_slot_rejects_other_owner(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id)
    assert slot.id is not None
    assert (
        await edit_slot(
            _slot_repo(session),
            slot_id=slot.id,
            specialist_id=_OTHER_SPECIALIST,
            weekday=2,
            time_hhmm="14:00",
            now=_NOW,
            tz=_TZ,
        )
        is None
    )


# ============================================================================
# use-case: remove_slot
# ============================================================================


async def test_remove_slot_deactivates_but_keeps_schedule_with_siblings(
    session: AsyncSession,
):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot_a = await _add_slot(session, schedule.id, weekday=0, time_hhmm="14:00")
    await _add_slot(session, schedule.id, weekday=2, time_hhmm="10:00")
    assert slot_a.id is not None

    removed = await remove_slot(
        _sched_repo(session),
        _slot_repo(session),
        slot_id=slot_a.id,
        specialist_id=_SPECIALIST,
        now=_NOW,
    )
    assert removed is not None
    assert removed.active is False
    # The removed slot drops from the active list; the sibling remains.
    remaining = await _slot_repo(session).list_for_schedule(schedule.id)
    assert [s.weekday for s in remaining] == [2]
    # Schedule still active because a slot remains.
    sched = await _sched_repo(session).get_for_specialist(schedule.id, _SPECIALIST)
    assert sched is not None
    assert sched.active is True


async def test_remove_last_slot_stops_schedule(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id)
    assert slot.id is not None

    removed = await remove_slot(
        _sched_repo(session),
        _slot_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        now=_NOW,
    )
    assert removed is not None
    sched = await _sched_repo(session).get_for_specialist(schedule.id, _SPECIALIST)
    assert sched is not None
    assert sched.active is False


async def test_remove_slot_rejects_other_owner(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id)
    assert slot.id is not None
    assert (
        await remove_slot(
            _sched_repo(session),
            _slot_repo(session),
            slot_id=slot.id,
            specialist_id=_OTHER_SPECIALIST,
            now=_NOW,
        )
        is None
    )
    # Schedule untouched.
    sched = await _sched_repo(session).get_for_specialist(schedule.id, _SPECIALIST)
    assert sched is not None
    assert sched.active is True


# ============================================================================
# use-case: stop_schedule
# ============================================================================


async def test_stop_schedule_deactivates(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    stopped = await stop_schedule(
        _sched_repo(session),
        schedule_id=schedule.id,
        specialist_id=_SPECIALIST,
        now=_NOW,
    )
    assert stopped is not None
    assert stopped.active is False


async def test_stop_schedule_rejects_other_owner(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    assert (
        await stop_schedule(
            _sched_repo(session),
            schedule_id=schedule.id,
            specialist_id=_OTHER_SPECIALIST,
            now=_NOW,
        )
        is None
    )


# ============================================================================
# use-cases: per-occurrence overrides (axis preservation)
# ============================================================================


async def _slot_with_repos(
    session: AsyncSession,
) -> tuple[RecurringSlot, SqlAlchemyRecurringSlotOverrideRepo]:
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id)
    assert slot.id is not None
    return slot, _ov_repo(session)


async def test_skip_occurrence_creates_skip(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    ov = await skip_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        now=_NOW,
    )
    assert ov is not None
    assert ov.skipped is True
    assert ov.moved_to is None


async def test_move_occurrence_creates_move(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    moved_to = datetime(2026, 6, 24, 13, 0, tzinfo=UTC)
    ov = await move_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        moved_to=moved_to,
        now=_NOW,
    )
    assert ov is not None
    assert ov.moved_to == moved_to
    assert ov.skipped is False


async def test_set_occurrence_comment_keeps_existing_move(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    moved_to = datetime(2026, 6, 24, 13, 0, tzinfo=UTC)
    await move_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        moved_to=moved_to,
        now=_NOW,
    )
    ov = await set_occurrence_comment(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        comment="особый",
        now=_NOW,
    )
    assert ov is not None
    assert ov.comment == "особый"
    assert ov.moved_to == moved_to  # move preserved
    assert ov.skipped is False


async def test_move_occurrence_keeps_comment_and_unskips(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    target = date(2026, 6, 22)
    # First comment + skip the date.
    await skip_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=target,
        now=_NOW,
    )
    await set_occurrence_comment(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=target,
        comment="перенос",
        now=_NOW,
    )
    moved_to = datetime(2026, 6, 24, 13, 0, tzinfo=UTC)
    ov = await move_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=target,
        moved_to=moved_to,
        now=_NOW,
    )
    assert ov is not None
    assert ov.moved_to == moved_to
    assert ov.comment == "перенос"  # comment preserved
    assert ov.skipped is False  # un-skipped


async def test_skip_occurrence_keeps_existing_comment(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    target = date(2026, 6, 22)
    await set_occurrence_comment(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=target,
        comment="заметка",
        now=_NOW,
    )
    ov = await skip_occurrence(
        _slot_repo(session),
        ov_repo,
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=target,
        now=_NOW,
    )
    assert ov is not None
    assert ov.skipped is True
    assert ov.comment == "заметка"  # comment preserved across skip


async def test_override_use_cases_reject_other_owner(session: AsyncSession):
    slot, ov_repo = await _slot_with_repos(session)
    assert slot.id is not None
    target = date(2026, 6, 22)
    assert (
        await skip_occurrence(
            _slot_repo(session),
            ov_repo,
            slot_id=slot.id,
            specialist_id=_OTHER_SPECIALIST,
            original_date=target,
            now=_NOW,
        )
        is None
    )
    assert (
        await move_occurrence(
            _slot_repo(session),
            ov_repo,
            slot_id=slot.id,
            specialist_id=_OTHER_SPECIALIST,
            original_date=target,
            moved_to=datetime(2026, 6, 24, 13, 0, tzinfo=UTC),
            now=_NOW,
        )
        is None
    )
    assert (
        await set_occurrence_comment(
            _slot_repo(session),
            ov_repo,
            slot_id=slot.id,
            specialist_id=_OTHER_SPECIALIST,
            original_date=target,
            comment="x",
            now=_NOW,
        )
        is None
    )
    # No override row leaked.
    assert await ov_repo.list_for_slot(slot.id) == []


# ============================================================================
# occurrence math: occurrences_landing_in
# ============================================================================


def test_occurrences_landing_in_weekly_from_today():
    slot = _slot()
    schedule = _schedule_obj()
    occ = occurrences_landing_in(
        slot, schedule, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, _TODAY
    )
    assert [o.origin_date for o in occ] == [
        date(2026, 6, 15),
        date(2026, 6, 22),
        date(2026, 6, 29),
        date(2026, 7, 6),
    ]
    # 14:00 Moscow (+03) → 11:00 UTC; virtual rows carry the slot, no id.
    assert occ[0].starts_at == _UTC_1400
    assert occ[0].id is None
    assert occ[0].slot_id == slot.id
    assert occ[0].comment == "регулярная"
    assert occ[0].recurring_mark is True


def test_occurrences_landing_in_excludes_past_dates():
    slot = _slot()
    schedule = _schedule_obj()
    occ = occurrences_landing_in(
        slot, schedule, [], date(2026, 6, 1), date(2026, 6, 23), _TZ, _TODAY
    )
    assert [o.origin_date for o in occ] == [date(2026, 6, 15), date(2026, 6, 22)]


def test_occurrences_landing_in_inactive_slot_is_empty():
    slot = _slot(active=False)
    schedule = _schedule_obj()
    assert (
        occurrences_landing_in(
            slot, schedule, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, _TODAY
        )
        == []
    )


def test_occurrences_landing_in_inactive_schedule_is_empty():
    slot = _slot()
    schedule = _schedule_obj(active=False)
    assert (
        occurrences_landing_in(
            slot, schedule, [], date(2026, 6, 15), date(2026, 7, 13), _TZ, _TODAY
        )
        == []
    )


def test_occurrences_landing_in_skip_removes_date():
    slot = _slot()
    schedule = _schedule_obj()
    ov = _override(original_date=date(2026, 6, 22), skipped=True)
    occ = occurrences_landing_in(
        slot, schedule, [ov], date(2026, 6, 15), date(2026, 7, 6), _TZ, _TODAY
    )
    dates = [o.origin_date for o in occ]
    assert date(2026, 6, 22) not in dates
    assert date(2026, 6, 15) in dates  # neighbour kept
    assert date(2026, 6, 29) in dates


def test_occurrences_landing_in_move_relands_and_frees_original():
    slot = _slot()
    schedule = _schedule_obj()
    # Move the 2026-06-22 (Mon) occurrence to Wed 2026-06-24 16:00 Moscow (13:00 UTC).
    moved_at = datetime(2026, 6, 24, 13, 0, tzinfo=UTC)
    ov = _override(original_date=date(2026, 6, 22), moved_to=moved_at)
    # The new day shows it, keyed to its original date, no 🔁 mark.
    on_wed = occurrences_landing_in(
        slot, schedule, [ov], date(2026, 6, 24), date(2026, 6, 25), _TZ, _TODAY
    )
    assert [o.origin_date for o in on_wed] == [date(2026, 6, 22)]
    assert on_wed[0].starts_at == moved_at
    assert on_wed[0].recurring_mark is False
    # The original Monday no longer shows the occurrence (slot freed).
    on_mon = occurrences_landing_in(
        slot, schedule, [ov], date(2026, 6, 22), date(2026, 6, 23), _TZ, _TODAY
    )
    assert on_mon == []


def test_occurrences_landing_in_comment_only_keeps_grid_date():
    slot = _slot()
    schedule = _schedule_obj()
    ov = _override(original_date=date(2026, 6, 22), comment="особый")
    occ = occurrences_landing_in(
        slot, schedule, [ov], date(2026, 6, 15), date(2026, 7, 6), _TZ, _TODAY
    )
    target = next(o for o in occ if o.origin_date == date(2026, 6, 22))
    assert target.comment == "особый"  # overridden comment
    # The grid date stays in place at its planned time.
    assert target.starts_at == datetime(2026, 6, 22, 11, 0, tzinfo=UTC)
    # Neighbour keeps the schedule's shared comment.
    other = next(o for o in occ if o.origin_date == date(2026, 6, 29))
    assert other.comment == "регулярная"


def test_occurrences_landing_in_ignores_moved_past_origin():
    slot = _slot()
    schedule = _schedule_obj()
    # An override on a past date (already materialised) is not re-expanded.
    past_move = _override(
        original_date=date(2026, 6, 8),  # before today
        moved_to=datetime(2026, 6, 16, 9, 0, tzinfo=UTC),
    )
    occ = occurrences_landing_in(
        slot, schedule, [past_move], date(2026, 6, 15), date(2026, 6, 23), _TZ, _TODAY
    )
    assert [o.origin_date for o in occ] == [date(2026, 6, 15), date(2026, 6, 22)]


# ============================================================================
# occurrence math: occurrences_in_window (aggregates slots, time-sorted)
# ============================================================================


def test_occurrences_in_window_aggregates_and_sorts():
    schedule = _schedule_obj()
    # Monday slot at 14:00 and a Wednesday slot at 10:00.
    mon = _slot(slot_id=1, weekday=0, time_hhmm="14:00")
    wed = _slot(slot_id=2, weekday=2, time_hhmm="10:00")
    occ = occurrences_in_window(
        schedule,
        [mon, wed],
        {},
        date(2026, 6, 15),
        date(2026, 6, 22),  # one week
        _TZ,
        _TODAY,
    )
    # Mon 06-15 14:00 then Wed 06-17 10:00 — sorted by instant.
    assert [(o.origin_date, o.slot_id) for o in occ] == [
        (date(2026, 6, 15), 1),
        (date(2026, 6, 17), 2),
    ]
    assert occ == sorted(occ, key=lambda o: o.starts_at)


def test_occurrences_in_window_two_slots_same_weekday():
    schedule = _schedule_obj()
    # Two slots both on Monday, different times — both land independently.
    early = _slot(slot_id=1, weekday=0, time_hhmm="10:00")
    late = _slot(slot_id=2, weekday=0, time_hhmm="14:00")
    occ = occurrences_in_window(
        schedule,
        [late, early],  # deliberately unsorted input
        {},
        date(2026, 6, 15),
        date(2026, 6, 16),  # just Monday
        _TZ,
        _TODAY,
    )
    assert [o.slot_id for o in occ] == [1, 2]  # 10:00 before 14:00
    assert occ[0].starts_at < occ[1].starts_at


def test_occurrences_in_window_applies_per_slot_overrides():
    schedule = _schedule_obj()
    mon = _slot(slot_id=1, weekday=0, time_hhmm="14:00")
    wed = _slot(slot_id=2, weekday=2, time_hhmm="10:00")
    # Skip the Wednesday occurrence via its slot's override list.
    overrides = {
        2: [_override(slot_id=2, original_date=date(2026, 6, 17), skipped=True)]
    }
    occ = occurrences_in_window(
        schedule,
        [mon, wed],
        overrides,
        date(2026, 6, 15),
        date(2026, 6, 22),
        _TZ,
        _TODAY,
    )
    assert [o.origin_date for o in occ] == [date(2026, 6, 15)]


# ============================================================================
# occurrence math: slot_taken_times
# ============================================================================


def test_slot_taken_times_on_grid_day():
    slot = _slot(weekday=0, time_hhmm="14:00")
    schedule = _schedule_obj()
    taken = slot_taken_times(slot, schedule, [], date(2026, 6, 22), _TZ, _TODAY)
    assert taken == {"14:00"}


def test_slot_taken_times_other_weekday_empty():
    slot = _slot(weekday=0, time_hhmm="14:00")
    schedule = _schedule_obj()
    # Tuesday is not the slot weekday.
    taken = slot_taken_times(slot, schedule, [], date(2026, 6, 16), _TZ, _TODAY)
    assert taken == set()


def test_slot_taken_times_move_frees_original_day():
    slot = _slot(weekday=0, time_hhmm="14:00")
    schedule = _schedule_obj()
    moved_at = datetime(2026, 6, 24, 13, 0, tzinfo=UTC)  # Wed 16:00 Moscow
    ov = _override(original_date=date(2026, 6, 22), moved_to=moved_at)
    # Original Monday freed.
    assert (
        slot_taken_times(slot, schedule, [ov], date(2026, 6, 22), _TZ, _TODAY) == set()
    )
    # Landing day occupied at the moved wall time (16:00).
    assert slot_taken_times(slot, schedule, [ov], date(2026, 6, 24), _TZ, _TODAY) == {
        "16:00"
    }


# ============================================================================
# occurrence math: next_occurrence
# ============================================================================


def test_next_occurrence_picks_nearest():
    slot = _slot(weekday=0, time_hhmm="14:00")
    schedule = _schedule_obj()
    nxt = next_occurrence(slot, schedule, [], _TZ, _TODAY)
    assert nxt is not None
    assert nxt.origin_date == _TODAY
    assert nxt.id is None


def test_next_occurrence_skips_to_following_when_first_skipped():
    slot = _slot(weekday=0, time_hhmm="14:00")
    schedule = _schedule_obj()
    ov = _override(original_date=_TODAY, skipped=True)
    nxt = next_occurrence(slot, schedule, [ov], _TZ, _TODAY)
    assert nxt is not None
    assert nxt.origin_date == date(2026, 6, 22)


def test_next_occurrence_none_when_beyond_horizon():
    far = _slot(
        weekday=0,
        start_date=date(2030, 1, 7),
        materialized_through=date(2030, 1, 7),
    )
    schedule = _schedule_obj()
    assert next_occurrence(far, schedule, [], _TZ, _TODAY) is None


def test_next_occurrence_none_for_inactive_slot():
    slot = _slot(active=False)
    schedule = _schedule_obj()
    assert next_occurrence(slot, schedule, [], _TZ, _TODAY) is None


# ============================================================================
# occurrence math: nearest_slot_landing_day
# ============================================================================


async def _ctx(session: AsyncSession):
    return await load_series_context(
        _sched_repo(session),
        _slot_repo(session),
        _ov_repo(session),
        specialist_id=_SPECIALIST,
        now=_NOW,
        tz=_TZ,
    )


async def test_nearest_slot_landing_day_forward(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    await _add_slot(session, schedule.id, weekday=0, start_date=date(2026, 6, 1))
    ctx = await _ctx(session)
    # Strictly after _TODAY (a Monday) → next Monday.
    landing = nearest_slot_landing_day(ctx, _TODAY, _TZ, forward=True)
    assert landing == date(2026, 6, 22)


async def test_nearest_slot_landing_day_backward_is_none(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    await _add_slot(session, schedule.id, weekday=0, start_date=date(2026, 6, 1))
    ctx = await _ctx(session)
    assert nearest_slot_landing_day(ctx, _TODAY, _TZ, forward=False) is None


async def test_nearest_slot_landing_day_none_without_slots(session: AsyncSession):
    await _schedule(session)  # schedule with no slots
    ctx = await _ctx(session)
    assert nearest_slot_landing_day(ctx, _TODAY, _TZ, forward=True) is None


# ============================================================================
# load_series_context
# ============================================================================


async def test_load_series_context_only_active_schedules(session: AsyncSession):
    active = await _schedule(session)
    assert active.id is not None
    slot_active = await _add_slot(session, active.id, weekday=0)
    assert slot_active.id is not None
    await skip_occurrence(
        _slot_repo(session),
        _ov_repo(session),
        slot_id=slot_active.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 22),
        now=_NOW,
    )

    stopped = await _schedule(session, comment="прекращена")
    assert stopped.id is not None
    await _add_slot(session, stopped.id, weekday=2)
    await stop_schedule(
        _sched_repo(session),
        schedule_id=stopped.id,
        specialist_id=_SPECIALIST,
        now=_NOW,
    )

    ctx = await _ctx(session)
    assert list(ctx.schedules) == [active.id]
    assert [s.schedule_id for s in ctx.slots] == [active.id]
    assert ctx.today == _TODAY
    # Helper accessors.
    assert ctx.schedule_for(ctx.slots[0]).id == active.id
    assert len(ctx.for_slot(slot_active.id)) == 1
    # A virtual slot id (None) maps to no overrides.
    assert ctx.for_slot(None) == []


async def test_load_series_context_inactive_slots_excluded(session: AsyncSession):
    schedule = await _schedule(session)
    assert schedule.id is not None
    keep = await _add_slot(session, schedule.id, weekday=0)
    drop = await _add_slot(session, schedule.id, weekday=2)
    assert keep.id is not None
    assert drop.id is not None
    await remove_slot(
        _sched_repo(session),
        _slot_repo(session),
        slot_id=drop.id,
        specialist_id=_SPECIALIST,
        now=_NOW,
    )
    ctx = await _ctx(session)
    assert [s.id for s in ctx.slots] == [keep.id]


# ============================================================================
# settle
# ============================================================================


async def _settle(session: AsyncSession) -> None:
    await settle(
        _sched_repo(session),
        _slot_repo(session),
        _ov_repo(session),
        _appt_repo(session),
        specialist_id=_SPECIALIST,
        now=_NOW,
        tz=_TZ,
    )


async def _list_appointments(session: AsyncSession) -> list:
    return await _appt_repo(session).list_for_specialist_between(
        _SPECIALIST,
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2027, 1, 1, tzinfo=UTC),
    )


async def _seed_past_slot(session: AsyncSession) -> RecurringSlot:
    """A schedule + slot whose grid started two Mondays ago, nothing frozen yet."""
    schedule = await _schedule(session)
    assert schedule.id is not None
    # start_date 2026-06-01 (Mon) ⇒ past occurrences 06-01, 06-08 await settle.
    return await _add_slot(
        session, schedule.id, weekday=0, time_hhmm="14:00", start_date=date(2026, 6, 1)
    )


async def test_settle_materializes_past_occurrences(session: AsyncSession):
    slot = await _seed_past_slot(session)
    assert slot.id is not None

    await _settle(session)

    rows = await _list_appointments(session)
    # Past Mondays 06-01 and 06-08 frozen; today (06-15) stays virtual.
    assert sorted(r.origin_date for r in rows) == [date(2026, 6, 1), date(2026, 6, 8)]
    assert all(r.slot_id == slot.id for r in rows)
    assert all(r.comment == "регулярная" for r in rows)  # schedule comment
    first = next(r for r in rows if r.origin_date == date(2026, 6, 1))
    assert first.starts_at == datetime(2026, 6, 1, 11, 0, tzinfo=UTC)

    fetched = await _slot_repo(session).get_for_specialist(slot.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.materialized_through == _TODAY


async def test_settle_is_idempotent(session: AsyncSession):
    await _seed_past_slot(session)
    await _settle(session)
    await _settle(session)  # second interaction the same day
    rows = await _list_appointments(session)
    assert len(rows) == 2  # no duplicates, guard short-circuits


async def test_settle_insert_idempotent_under_replay(session: AsyncSession):
    slot = await _seed_past_slot(session)
    assert slot.id is not None
    await _settle(session)
    # Simulate a replayed settle: rewind the guard and run again.
    await _slot_repo(session).set_materialized_through(
        slot.id, materialized_through=date(2026, 6, 1)
    )
    await _settle(session)
    rows = await _list_appointments(session)
    assert len(rows) == 2  # insert-or-ignore prevented duplicates


async def test_settle_without_active_schedule_is_noop(session: AsyncSession):
    await _settle(session)
    assert await _list_appointments(session) == []


async def test_settle_skips_skipped_past_date(session: AsyncSession):
    slot = await _seed_past_slot(session)
    assert slot.id is not None
    await skip_occurrence(
        _slot_repo(session),
        _ov_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 8),
        now=_NOW,
    )
    await _settle(session)
    rows = await _list_appointments(session)
    # Only 2026-06-01 frozen; the skipped 06-08 leaves no row.
    assert [r.origin_date for r in rows] == [date(2026, 6, 1)]


async def test_settle_moves_past_date_to_new_time_keeps_origin(session: AsyncSession):
    slot = await _seed_past_slot(session)
    assert slot.id is not None
    moved_at = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    await move_occurrence(
        _slot_repo(session),
        _ov_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 8),
        moved_to=moved_at,
        now=_NOW,
    )
    await _settle(session)
    rows = await _list_appointments(session)
    moved = next(r for r in rows if r.origin_date == date(2026, 6, 8))
    assert moved.starts_at == moved_at  # lands at moved_to
    assert moved.origin_date == date(2026, 6, 8)  # origin kept


async def test_settle_writes_effective_override_comment(session: AsyncSession):
    slot = await _seed_past_slot(session)
    assert slot.id is not None
    await set_occurrence_comment(
        _slot_repo(session),
        _ov_repo(session),
        slot_id=slot.id,
        specialist_id=_SPECIALIST,
        original_date=date(2026, 6, 8),
        comment="индивидуальный",
        now=_NOW,
    )
    await _settle(session)
    rows = await _list_appointments(session)
    overridden = next(r for r in rows if r.origin_date == date(2026, 6, 8))
    assert overridden.comment == "индивидуальный"  # override wins
    plain = next(r for r in rows if r.origin_date == date(2026, 6, 1))
    assert plain.comment == "регулярная"  # falls back to schedule comment


async def test_settle_advances_materialized_through_without_inserts(
    session: AsyncSession,
):
    # A slot whose grid starts today has no past dates, but settle still advances
    # the guard from a stale materialized_through to today.
    schedule = await _schedule(session)
    assert schedule.id is not None
    slot = await _add_slot(session, schedule.id, weekday=0, start_date=_TODAY)
    assert slot.id is not None
    await _settle(session)
    assert await _list_appointments(session) == []
    fetched = await _slot_repo(session).get_for_specialist(slot.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.materialized_through == _TODAY
