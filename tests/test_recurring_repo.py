"""Repository tests for the multi-slot recurring schedule storage layer.

Covers the three SQLAlchemy repos in `src/infrastructure/recurring_repo.py`
(schedules → slots → per-date overrides) plus the slot/origin-date idempotency
of `appointments_repo.insert_occurrence`.
"""

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appointment import Appointment
from src.domain.recurring import (
    RecurringSchedule,
    RecurringSlot,
)
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import ClientORM
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.specialists_repo import SpecialistORM

_SPECIALIST = 1
_OTHER_SPECIALIST = 2
_CLIENT = 7
_NOW = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)


async def _seed_owners(session: AsyncSession) -> None:
    # FK-bearing inserts (schedules/slots/appointments) reference these rows;
    # seed both specialists so ownership tests can exercise the other-owner path.
    session.add(SpecialistORM(id=_SPECIALIST, invite_token="tok-1", created_at=_NOW))
    session.add(
        SpecialistORM(id=_OTHER_SPECIALIST, invite_token="tok-2", created_at=_NOW)
    )
    session.add(
        ClientORM(
            id=_CLIENT,
            specialist_id=_SPECIALIST,
            child_name="Дитя",
            contact_name="Родитель",
            status="active",
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    await session.commit()


def _schedule(**overrides: object) -> RecurringSchedule:
    base = {
        "id": None,
        "specialist_id": _SPECIALIST,
        "client_id": _CLIENT,
        "comment": None,
        "active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return RecurringSchedule(**base)  # type: ignore[arg-type]


def _slot(schedule_id: int, **overrides: object) -> RecurringSlot:
    base = {
        "id": None,
        "schedule_id": schedule_id,
        "weekday": 0,
        "time_hhmm": "14:00",
        "active": True,
        "start_date": date(2026, 6, 1),
        "materialized_through": date(2026, 6, 1),
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return RecurringSlot(**base)  # type: ignore[arg-type]


# --- RecurringScheduleRepo --------------------------------------------------


async def test_schedule_round_trip(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule(comment="каждый понедельник"))
    assert saved.id is not None

    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.client_id == _CLIENT
    assert fetched.comment == "каждый понедельник"
    assert fetched.active is True
    assert fetched.created_at == _NOW


async def test_schedule_get_for_specialist_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule())
    assert saved.id is not None
    assert await repo.get_for_specialist(saved.id, _OTHER_SPECIALIST) is None


async def test_schedule_get_missing_returns_none(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    assert await repo.get_for_specialist(404, _SPECIALIST) is None


async def test_list_active_only_and_ordered(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    first = await repo.add(_schedule(comment="первое"))
    second = await repo.add(_schedule(comment="второе"))
    inactive = await repo.add(_schedule(comment="неактивное", active=False))
    assert first.id is not None
    assert second.id is not None
    assert inactive.id is not None

    active = await repo.list_active_for_specialist(_SPECIALIST)
    # Active-only, ordered by id ascending; the inactive schedule is excluded.
    assert [s.id for s in active] == [first.id, second.id]


async def test_list_active_scoped_to_owner(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    await repo.add(_schedule())
    assert await repo.list_active_for_specialist(_OTHER_SPECIALIST) == []


async def test_set_active_removes_from_active_list(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule())
    assert saved.id is not None
    assert len(await repo.list_active_for_specialist(_SPECIALIST)) == 1

    updated_at = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)
    stopped = await repo.set_active(
        saved.id, _SPECIALIST, active=False, updated_at=updated_at
    )
    assert stopped is not None
    assert stopped.active is False
    assert stopped.updated_at == updated_at
    assert await repo.list_active_for_specialist(_SPECIALIST) == []


async def test_set_comment_updates_value(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule())
    assert saved.id is not None

    updated_at = datetime(2026, 6, 6, 9, 0, tzinfo=UTC)
    updated = await repo.set_comment(
        saved.id, _SPECIALIST, comment="новый", updated_at=updated_at
    )
    assert updated is not None
    assert updated.comment == "новый"
    assert updated.updated_at == updated_at

    cleared = await repo.set_comment(
        saved.id, _SPECIALIST, comment=None, updated_at=updated_at
    )
    assert cleared is not None
    assert cleared.comment is None


async def test_set_comment_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule())
    assert saved.id is not None
    assert (
        await repo.set_comment(
            saved.id, _OTHER_SPECIALIST, comment="x", updated_at=_NOW
        )
        is None
    )


async def test_set_active_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringScheduleRepo(session)
    saved = await repo.add(_schedule())
    assert saved.id is not None
    assert (
        await repo.set_active(
            saved.id, _OTHER_SPECIALIST, active=False, updated_at=_NOW
        )
        is None
    )
    # The schedule stays active because the rejected call never touched it.
    assert len(await repo.list_active_for_specialist(_SPECIALIST)) == 1


# --- RecurringSlotRepo ------------------------------------------------------


async def test_slot_round_trip(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id, weekday=2, time_hhmm="10:30"))
    assert saved.id is not None

    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.schedule_id == sched.id
    assert fetched.weekday == 2
    assert fetched.time_hhmm == "10:30"
    assert fetched.active is True
    assert fetched.start_date == date(2026, 6, 1)


async def test_list_for_schedule_active_only_and_ordered(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    mon = await repo.add(_slot(sched.id, weekday=0))
    wed = await repo.add(_slot(sched.id, weekday=2))
    removed = await repo.add(_slot(sched.id, weekday=4, active=False))
    assert mon.id is not None
    assert wed.id is not None
    assert removed.id is not None

    slots = await repo.list_for_schedule(sched.id)
    # Removed (inactive) slots keep their history but never list.
    assert [s.id for s in slots] == [mon.id, wed.id]


async def test_two_slots_same_weekday_coexist(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    morning = await repo.add(_slot(sched.id, weekday=0, time_hhmm="09:00"))
    evening = await repo.add(_slot(sched.id, weekday=0, time_hhmm="17:00"))
    assert morning.id is not None
    assert evening.id is not None

    slots = await repo.list_for_schedule(sched.id)
    assert {s.time_hhmm for s in slots} == {"09:00", "17:00"}


async def test_slot_get_for_specialist_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None
    # Ownership flows through the slot's schedule: the other specialist owns no
    # schedule that contains this slot, so the slot is invisible to them.
    assert await repo.get_for_specialist(saved.id, _OTHER_SPECIALIST) is None


async def test_slot_get_missing_returns_none(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringSlotRepo(session)
    assert await repo.get_for_specialist(404, _SPECIALIST) is None


async def test_slot_set_active(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None

    updated_at = datetime(2026, 6, 7, 8, 0, tzinfo=UTC)
    deactivated = await repo.set_active(
        saved.id, _SPECIALIST, active=False, updated_at=updated_at
    )
    assert deactivated is not None
    assert deactivated.active is False
    assert deactivated.updated_at == updated_at
    assert await repo.list_for_schedule(sched.id) == []


async def test_slot_set_active_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None
    assert (
        await repo.set_active(
            saved.id, _OTHER_SPECIALIST, active=False, updated_at=_NOW
        )
        is None
    )


async def test_slot_set_materialized_through(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None

    await repo.set_materialized_through(
        saved.id, materialized_through=date(2026, 6, 20)
    )
    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.materialized_through == date(2026, 6, 20)


async def test_slot_update_rule(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None

    updated_at = datetime(2026, 6, 8, 7, 0, tzinfo=UTC)
    updated = await repo.update_rule(
        saved.id,
        _SPECIALIST,
        weekday=2,
        time_hhmm="10:30",
        start_date=date(2026, 6, 10),
        materialized_through=date(2026, 6, 9),
        updated_at=updated_at,
    )
    assert updated is not None
    assert updated.weekday == 2
    assert updated.time_hhmm == "10:30"
    assert updated.start_date == date(2026, 6, 10)
    assert updated.materialized_through == date(2026, 6, 9)
    assert updated.updated_at == updated_at


async def test_slot_update_rule_rejects_other_owner(session: AsyncSession):
    await _seed_owners(session)
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    repo = SqlAlchemyRecurringSlotRepo(session)
    saved = await repo.add(_slot(sched.id))
    assert saved.id is not None
    assert (
        await repo.update_rule(
            saved.id,
            _OTHER_SPECIALIST,
            weekday=2,
            time_hhmm="10:30",
            start_date=date(2026, 6, 10),
            materialized_through=date(2026, 6, 9),
            updated_at=_NOW,
        )
        is None
    )


# --- RecurringSlotOverrideRepo ----------------------------------------------


async def _slot_for_overrides(session: AsyncSession, **slot_overrides: object) -> int:
    sched = await SqlAlchemyRecurringScheduleRepo(session).add(_schedule())
    assert sched.id is not None
    slot = await SqlAlchemyRecurringSlotRepo(session).add(
        _slot(sched.id, **slot_overrides)
    )
    assert slot.id is not None
    return slot.id


async def test_override_upsert_overwrites_all_axes(session: AsyncSession):
    await _seed_owners(session)
    slot_id = await _slot_for_overrides(session)
    repo = SqlAlchemyRecurringSlotOverrideRepo(session)
    original = date(2026, 6, 15)

    skipped = await repo.upsert(
        slot_id,
        original,
        skipped=True,
        moved_to=None,
        comment="пропуск",
        created_at=_NOW,
    )
    assert skipped.skipped is True
    assert skipped.moved_to is None
    assert skipped.comment == "пропуск"
    assert len(await repo.list_for_slot(slot_id)) == 1

    moved_to = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    # Re-acting on the same (slot_id, original_date) overwrites every axis, not
    # just the one the caller cares about this time.
    moved = await repo.upsert(
        slot_id,
        original,
        skipped=False,
        moved_to=moved_to,
        comment=None,
        created_at=_NOW,
    )
    assert moved.skipped is False
    assert moved.moved_to == moved_to
    assert moved.comment is None

    rows = await repo.list_for_slot(slot_id)
    # Idempotent on the unique key: one row, fully overwritten.
    assert len(rows) == 1
    assert rows[0].moved_to == moved_to
    assert rows[0].skipped is False
    assert rows[0].comment is None


async def test_override_list_for_slot_scoped(session: AsyncSession):
    await _seed_owners(session)
    slot_a = await _slot_for_overrides(session, weekday=0)
    slot_b = await _slot_for_overrides(session, weekday=2)
    repo = SqlAlchemyRecurringSlotOverrideRepo(session)
    await repo.upsert(
        slot_a,
        date(2026, 6, 15),
        skipped=True,
        moved_to=None,
        comment=None,
        created_at=_NOW,
    )
    await repo.upsert(
        slot_b,
        date(2026, 6, 17),
        skipped=True,
        moved_to=None,
        comment=None,
        created_at=_NOW,
    )

    rows = await repo.list_for_slot(slot_a)
    assert len(rows) == 1
    assert rows[0].slot_id == slot_a
    assert rows[0].original_date == date(2026, 6, 15)


async def test_override_list_for_specialist_joins_through_schedules(
    session: AsyncSession,
):
    await _seed_owners(session)
    # Two slots under the owner's schedules, plus a slot under another
    # specialist's schedule that must NOT appear.
    slot_a = await _slot_for_overrides(session, weekday=0)
    slot_b = await _slot_for_overrides(session, weekday=2)
    other_sched = await SqlAlchemyRecurringScheduleRepo(session).add(
        _schedule(specialist_id=_OTHER_SPECIALIST)
    )
    assert other_sched.id is not None
    other_slot = await SqlAlchemyRecurringSlotRepo(session).add(_slot(other_sched.id))
    assert other_slot.id is not None

    repo = SqlAlchemyRecurringSlotOverrideRepo(session)
    await repo.upsert(
        slot_a,
        date(2026, 6, 15),
        skipped=True,
        moved_to=None,
        comment=None,
        created_at=_NOW,
    )
    await repo.upsert(
        slot_b,
        date(2026, 6, 17),
        skipped=True,
        moved_to=None,
        comment=None,
        created_at=_NOW,
    )
    await repo.upsert(
        other_slot.id,
        date(2026, 6, 18),
        skipped=True,
        moved_to=None,
        comment=None,
        created_at=_NOW,
    )

    rows = await repo.list_for_specialist(_SPECIALIST)
    assert {r.slot_id for r in rows} == {slot_a, slot_b}


async def test_override_list_for_specialist_empty(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyRecurringSlotOverrideRepo(session)
    assert await repo.list_for_specialist(_SPECIALIST) == []


# --- appointments_repo.insert_occurrence idempotency ------------------------


async def test_insert_occurrence_is_idempotent(session: AsyncSession):
    await _seed_owners(session)
    slot_id = await _slot_for_overrides(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    occurrence = Appointment(
        id=None,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        starts_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        comment="регулярная",
        created_at=_NOW,
        updated_at=_NOW,
        slot_id=slot_id,
        origin_date=date(2026, 6, 1),
    )
    assert await repo.insert_occurrence(occurrence) is True
    # Same (slot_id, origin_date) → no second row, returns False.
    assert await repo.insert_occurrence(occurrence) is False

    rows = await repo.list_for_specialist_between(
        _SPECIALIST,
        start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
    )
    assert len(rows) == 1
    assert rows[0].slot_id == slot_id
    assert rows[0].origin_date == date(2026, 6, 1)


async def test_one_off_appointment_has_null_slot_columns(session: AsyncSession):
    await _seed_owners(session)
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(
        Appointment(
            id=None,
            specialist_id=_SPECIALIST,
            client_id=_CLIENT,
            starts_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            comment=None,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    assert saved.slot_id is None
    assert saved.origin_date is None
