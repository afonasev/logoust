from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appointment import Appointment
from src.domain.recurring import RecurringAppointment
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)

_SPECIALIST = 1
_CLIENT = 7
_NOW = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)


def _series(**overrides: object) -> RecurringAppointment:
    base = {
        "id": None,
        "specialist_id": _SPECIALIST,
        "client_id": _CLIENT,
        "weekday": 0,
        "time_hhmm": "14:00",
        "comment": None,
        "active": True,
        "start_date": date(2026, 6, 1),
        "materialized_through": date(2026, 6, 1),
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return RecurringAppointment(**base)  # type: ignore[arg-type]


async def test_series_round_trip(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series(comment="каждый понедельник"))
    assert saved.id is not None

    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.weekday == 0
    assert fetched.time_hhmm == "14:00"
    assert fetched.comment == "каждый понедельник"
    assert fetched.active is True
    assert fetched.start_date == date(2026, 6, 1)


async def test_get_for_specialist_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    assert await repo.get_for_specialist(saved.id, 999) is None


async def test_set_active_removes_from_active_list(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    assert len(await repo.list_active_for_specialist(_SPECIALIST)) == 1

    stopped = await repo.set_active(
        saved.id, _SPECIALIST, active=False, updated_at=_NOW
    )
    assert stopped is not None
    assert stopped.active is False
    assert await repo.list_active_for_specialist(_SPECIALIST) == []


async def test_set_active_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    assert await repo.set_active(saved.id, 999, active=False, updated_at=_NOW) is None


async def test_set_materialized_through(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    await repo.set_materialized_through(
        saved.id, materialized_through=date(2026, 6, 20)
    )
    fetched = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert fetched is not None
    assert fetched.materialized_through == date(2026, 6, 20)


async def test_update_rule(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    updated = await repo.update_rule(
        saved.id,
        _SPECIALIST,
        weekday=2,
        time_hhmm="10:30",
        comment="новый",
        start_date=date(2026, 6, 10),
        materialized_through=date(2026, 6, 5),
        updated_at=_NOW,
    )
    assert updated is not None
    assert updated.weekday == 2
    assert updated.time_hhmm == "10:30"
    assert updated.comment == "новый"
    assert updated.start_date == date(2026, 6, 10)


async def test_update_rule_rejects_other_owner(session: AsyncSession):
    repo = SqlAlchemyRecurringRepo(session)
    saved = await repo.add(_series())
    assert saved.id is not None
    assert (
        await repo.update_rule(
            saved.id,
            999,
            weekday=2,
            time_hhmm="10:30",
            comment=None,
            start_date=date(2026, 6, 10),
            materialized_through=date(2026, 6, 5),
            updated_at=_NOW,
        )
        is None
    )


async def test_exception_upsert_skip_then_move(session: AsyncSession):
    series_repo = SqlAlchemyRecurringRepo(session)
    series = await series_repo.add(_series())
    assert series.id is not None
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)

    skip = await exc_repo.upsert(
        series.id, date(2026, 6, 15), new_starts_at=None, created_at=_NOW
    )
    assert skip.new_starts_at is None
    rows = await exc_repo.list_for_series(series.id)
    assert len(rows) == 1

    moved_to = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    moved = await exc_repo.upsert(
        series.id, date(2026, 6, 15), new_starts_at=moved_to, created_at=_NOW
    )
    assert moved.new_starts_at == moved_to
    # Upsert overwrote the existing row rather than adding a second one.
    rows = await exc_repo.list_for_series(series.id)
    assert len(rows) == 1
    assert rows[0].new_starts_at == moved_to


async def test_exception_list_for_specialist(session: AsyncSession):
    series_repo = SqlAlchemyRecurringRepo(session)
    series = await series_repo.add(_series())
    assert series.id is not None
    exc_repo = SqlAlchemyRecurringExceptionsRepo(session)
    await exc_repo.upsert(
        series.id, date(2026, 6, 15), new_starts_at=None, created_at=_NOW
    )
    rows = await exc_repo.list_for_specialist(_SPECIALIST)
    assert len(rows) == 1
    assert rows[0].original_date == date(2026, 6, 15)


async def test_insert_occurrence_is_idempotent(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    occurrence = Appointment(
        id=None,
        specialist_id=_SPECIALIST,
        client_id=_CLIENT,
        starts_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
        comment="регулярная",
        created_at=_NOW,
        updated_at=_NOW,
        series_id=1,
        origin_date=date(2026, 6, 1),
    )
    assert await repo.insert_occurrence(occurrence) is True
    # Same (series_id, origin_date) → no second row, returns False.
    assert await repo.insert_occurrence(occurrence) is False

    rows = await repo.list_for_specialist_between(
        _SPECIALIST,
        start=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
    )
    assert len(rows) == 1
    assert rows[0].series_id == 1
    assert rows[0].origin_date == date(2026, 6, 1)


async def test_one_off_appointment_has_null_series_columns(session: AsyncSession):
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
    assert saved.series_id is None
    assert saved.origin_date is None
