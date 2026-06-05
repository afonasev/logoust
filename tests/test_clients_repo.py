from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.client import Client, ClientStatus
from src.infrastructure.clients_repo import (
    ClientORM,
    SqlAlchemyClientsRepo,
    to_domain,
)


def _make(
    *,
    specialist_id: int = 1,
    child_name: str = "Петя",
    status: ClientStatus = ClientStatus.ACTIVE,
) -> Client:
    now = datetime.now(UTC)
    return Client(
        id=None,
        specialist_id=specialist_id,
        child_name=child_name,
        contact_name="Мама",
        contact_phone="+79161234567",
        contact_telegram=None,
        extra_contacts=None,
        note=None,
        status=status,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )


async def test_add_and_get_for_specialist(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    found = await repo.get_for_specialist(saved.id, 1)
    assert found is not None
    assert found.child_name == "Петя"
    assert found.status is ClientStatus.ACTIVE


async def test_get_for_specialist_isolated_by_owner(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make(specialist_id=1))
    assert saved.id is not None
    assert await repo.get_for_specialist(saved.id, 2) is None


async def test_list_by_status_sorted_and_filtered(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    await repo.add(_make(child_name="Яков"))
    await repo.add(_make(child_name="Аня"))
    await repo.add(_make(child_name="Архивный", status=ClientStatus.ARCHIVED))

    active = await repo.list_by_status(1, ClientStatus.ACTIVE)
    assert [c.child_name for c in active] == ["Аня", "Яков"]

    archived = await repo.list_by_status(1, ClientStatus.ARCHIVED)
    assert [c.child_name for c in archived] == ["Архивный"]


async def test_list_by_status_excludes_other_specialist(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    await repo.add(_make(specialist_id=1, child_name="Мой"))
    await repo.add(_make(specialist_id=2, child_name="Чужой"))
    mine = await repo.list_by_status(1, ClientStatus.ACTIVE)
    assert [c.child_name for c in mine] == ["Мой"]


async def test_list_active_orders_by_name_and_paginates(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    for name in ["Яков", "Аня", "Боря"]:
        await repo.add(_make(child_name=name))
    archived = await repo.add(
        _make(child_name="Архивный", status=ClientStatus.ARCHIVED)
    )
    assert archived.id is not None

    page0 = await repo.list_active(1, limit=2, offset=0)
    assert [c.child_name for c in page0] == ["Аня", "Боря"]  # archived excluded
    page1 = await repo.list_active(1, limit=2, offset=2)
    assert [c.child_name for c in page1] == ["Яков"]


async def test_list_archived_orders_desc_and_paginates(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for i, name in enumerate(["A", "B", "C"]):
        saved = await repo.add(_make(child_name=name))
        assert saved.id is not None
        await repo.set_status(
            saved.id,
            1,
            ClientStatus.ARCHIVED,
            archived_at=base.replace(day=1 + i),  # A=01, B=02, C=03
            updated_at=base,
        )

    page0 = await repo.list_archived(1, limit=2, offset=0)
    assert [c.child_name for c in page0] == ["C", "B"]  # самые свежие сверху
    page1 = await repo.list_archived(1, limit=2, offset=2)
    assert [c.child_name for c in page1] == ["A"]


async def test_update_fields_updates_and_returns(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    ts = datetime(2026, 6, 4, 10, 0, tzinfo=UTC)
    updated = await repo.update_fields(saved.id, 1, {"note": "важно"}, updated_at=ts)
    assert updated is not None
    assert updated.note == "важно"
    assert updated.updated_at == ts


async def test_update_fields_returns_none_for_other_owner(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make(specialist_id=1))
    assert saved.id is not None
    result = await repo.update_fields(
        saved.id, 2, {"note": "x"}, updated_at=datetime.now(UTC)
    )
    assert result is None


async def test_set_status_archive_and_restore(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make())
    assert saved.id is not None
    ts = datetime(2026, 6, 4, 10, 0, tzinfo=UTC)

    archived = await repo.set_status(
        saved.id, 1, ClientStatus.ARCHIVED, archived_at=ts, updated_at=ts
    )
    assert archived is not None
    assert archived.status is ClientStatus.ARCHIVED
    assert archived.archived_at == ts

    restored = await repo.set_status(
        saved.id, 1, ClientStatus.ACTIVE, archived_at=None, updated_at=ts
    )
    assert restored is not None
    assert restored.status is ClientStatus.ACTIVE
    assert restored.archived_at is None


async def test_set_status_returns_none_for_other_owner(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    saved = await repo.add(_make(specialist_id=1))
    assert saved.id is not None
    result = await repo.set_status(
        saved.id,
        2,
        ClientStatus.ARCHIVED,
        archived_at=None,
        updated_at=datetime.now(UTC),
    )
    assert result is None


def test_to_domain_maps_fields():
    now = datetime(2026, 6, 4, tzinfo=UTC)
    orm = ClientORM(
        id=5,
        specialist_id=3,
        child_name="Лиза",
        contact_name="Папа",
        contact_phone=None,
        contact_telegram="masha",
        extra_contacts=None,
        note=None,
        status="archived",
        archived_at=now,
        created_at=now,
        updated_at=now,
    )
    domain = to_domain(orm)
    assert domain.id == 5
    assert domain.specialist_id == 3
    assert domain.status is ClientStatus.ARCHIVED
    assert domain.contact_telegram == "masha"


def test_orm_repr_includes_child_name():
    now = datetime(2026, 6, 4, tzinfo=UTC)
    orm = ClientORM(
        id=1,
        specialist_id=1,
        child_name="Петя",
        contact_name="Мама",
        contact_phone=None,
        contact_telegram=None,
        extra_contacts=None,
        note=None,
        status="active",
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    assert "Петя" in repr(orm)
