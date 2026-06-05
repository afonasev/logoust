from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.appointment import Appointment
from src.infrastructure.appointments_repo import (
    AppointmentORM,
    SqlAlchemyAppointmentsRepo,
    to_domain,
)

_SPECIALIST = 1
_CLIENT = 7


def _make(
    *,
    specialist_id: int = _SPECIALIST,
    client_id: int = _CLIENT,
    starts_at: datetime | None = None,
    comment: str | None = None,
) -> Appointment:
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    return Appointment(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        starts_at=starts_at or now,
        comment=comment,
        created_at=now,
        updated_at=now,
    )


async def test_add_and_get_for_specialist(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(_make(comment="первое занятие"))
    assert saved.id is not None
    found = await repo.get_for_specialist(saved.id, _SPECIALIST)
    assert found is not None
    assert found.comment == "первое занятие"


async def test_get_for_specialist_isolated_by_owner(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    assert await repo.get_for_specialist(saved.id, 999) is None


async def test_list_future_for_specialist_sorted_asc(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    base = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    await repo.add(_make(starts_at=base.replace(day=12)))
    await repo.add(_make(starts_at=base.replace(day=10)))
    since = datetime(2026, 6, 9, tzinfo=UTC)
    rows = await repo.list_future_for_specialist(_SPECIALIST, since=since)
    assert [r.starts_at.day for r in rows] == [10, 12]


async def test_list_future_excludes_before_boundary(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await repo.add(_make(starts_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC)))
    since = datetime(2026, 6, 9, tzinfo=UTC)
    assert await repo.list_future_for_specialist(_SPECIALIST, since=since) == []


async def test_list_past_for_specialist_desc_and_paginated(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    base = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    for day in (1, 2, 3):
        await repo.add(_make(starts_at=base.replace(day=day)))
    before = datetime(2026, 6, 9, tzinfo=UTC)
    page0 = await repo.list_past_for_specialist(
        _SPECIALIST, before=before, limit=2, offset=0
    )
    assert [r.starts_at.day for r in page0] == [3, 2]
    page1 = await repo.list_past_for_specialist(
        _SPECIALIST, before=before, limit=2, offset=2
    )
    assert [r.starts_at.day for r in page1] == [1]


async def test_list_for_specialist_between_filters_range(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    await repo.add(_make(starts_at=datetime(2026, 6, 10, 8, 0, tzinfo=UTC)))  # before
    inside = await repo.add(_make(starts_at=datetime(2026, 6, 10, 12, 0, tzinfo=UTC)))
    await repo.add(_make(starts_at=datetime(2026, 6, 11, 0, 0, tzinfo=UTC)))  # at end
    rows = await repo.list_for_specialist_between(
        _SPECIALIST,
        start=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
        end=datetime(2026, 6, 11, 0, 0, tzinfo=UTC),
    )
    assert [r.id for r in rows] == [inside.id]


async def test_client_scoped_listings(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    future = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
    past = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    await repo.add(_make(client_id=_CLIENT, starts_at=future))
    await repo.add(_make(client_id=_CLIENT, starts_at=past))
    await repo.add(_make(client_id=99, starts_at=future))
    since = datetime(2026, 6, 9, tzinfo=UTC)
    mine_future = await repo.list_future_for_client(_SPECIALIST, _CLIENT, since=since)
    assert [r.client_id for r in mine_future] == [_CLIENT]
    mine_past = await repo.list_past_for_client(
        _SPECIALIST, _CLIENT, before=since, limit=10, offset=0
    )
    assert [r.client_id for r in mine_past] == [_CLIENT]


async def test_update_starts_at(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    new_time = datetime(2026, 7, 1, 15, 0, tzinfo=UTC)
    ts = datetime(2026, 6, 5, tzinfo=UTC)
    moved = await repo.update_starts_at(
        saved.id, _SPECIALIST, starts_at=new_time, updated_at=ts
    )
    assert moved is not None
    assert moved.starts_at == new_time
    assert moved.updated_at == ts


async def test_update_starts_at_other_owner_returns_none(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    result = await repo.update_starts_at(
        saved.id, 999, starts_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    assert result is None


async def test_delete_owned_and_foreign(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    assert await repo.delete(saved.id, 999) is False
    assert await repo.delete(saved.id, _SPECIALIST) is True
    assert await repo.get_for_specialist(saved.id, _SPECIALIST) is None


def test_to_domain_maps_fields():
    now = datetime(2026, 6, 4, tzinfo=UTC)
    orm = AppointmentORM(
        id=3,
        specialist_id=1,
        client_id=2,
        starts_at=now,
        comment="x",
        created_at=now,
        updated_at=now,
    )
    domain = to_domain(orm)
    assert domain.id == 3
    assert domain.client_id == 2
    assert domain.comment == "x"


def test_orm_repr_includes_client():
    now = datetime(2026, 6, 4, tzinfo=UTC)
    orm = AppointmentORM(
        id=1,
        specialist_id=1,
        client_id=42,
        starts_at=now,
        comment=None,
        created_at=now,
        updated_at=now,
    )
    assert "42" in repr(orm)
