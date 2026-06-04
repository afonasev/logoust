from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.specialist import Specialist
from src.infrastructure.specialists_repo import (
    SpecialistORM,
    SqlAlchemySpecialistsRepo,
    to_domain,
)


def _make(token: str) -> Specialist:
    return Specialist(
        id=None,
        invite_token=token,
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
    )


async def test_add_and_find_by_token(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    saved = await repo.add(_make("token-a"))
    assert saved.id is not None

    found = await repo.find_by_token("token-a")
    assert found is not None
    assert found.id == saved.id
    assert found.invite_token == "token-a"


async def test_find_by_token_returns_none_when_missing(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    assert await repo.find_by_token("nope") is None


async def test_find_by_chat_id_returns_welcomed_specialist(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    saved = await repo.add(_make("token-chat"))
    assert saved.id is not None
    await repo.mark_welcomed(
        saved.id,
        telegram_chat_id=777,
        telegram_username="ivanov",
        welcomed_at=datetime.now(UTC),
    )
    found = await repo.find_by_chat_id(777)
    assert found is not None
    assert found.id == saved.id


async def test_find_by_chat_id_returns_none_when_missing(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    assert await repo.find_by_chat_id(123) is None


async def test_mark_welcomed_sets_fields(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    saved = await repo.add(_make("token-b"))
    assert saved.id is not None
    welcomed_at = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    await repo.mark_welcomed(
        saved.id,
        telegram_chat_id=42,
        telegram_username="ivanov",
        welcomed_at=welcomed_at,
    )
    found = await repo.find_by_token("token-b")
    assert found is not None
    assert found.telegram_chat_id == 42
    assert found.telegram_username == "ivanov"
    assert found.welcomed_at is not None


async def test_mark_welcomed_is_idempotent(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    saved = await repo.add(_make("token-c"))
    assert saved.id is not None
    first = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    second = datetime(2026, 5, 27, 13, 0, tzinfo=UTC)
    await repo.mark_welcomed(
        saved.id,
        telegram_chat_id=10,
        telegram_username=None,
        welcomed_at=first,
    )
    await repo.mark_welcomed(
        saved.id,
        telegram_chat_id=99,
        telegram_username="other",
        welcomed_at=second,
    )
    found = await repo.find_by_token("token-c")
    assert found is not None
    assert found.telegram_chat_id == 10
    assert found.telegram_username is None


async def test_mark_welcomed_unknown_id_raises(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    with pytest.raises(ValueError, match="Specialist with id 999 not found"):
        await repo.mark_welcomed(
            999,
            telegram_chat_id=1,
            telegram_username=None,
            welcomed_at=datetime.now(UTC),
        )


def test_to_domain_maps_orm_fields():
    orm = SpecialistORM(
        id=7,
        invite_token="abc",
        telegram_chat_id=1,
        telegram_username="u",
        welcomed_at=None,
        created_at=datetime(2026, 5, 27, tzinfo=UTC),
    )
    domain = to_domain(orm)
    assert domain.id == 7
    assert domain.invite_token == "abc"
    assert domain.telegram_chat_id == 1
    assert domain.telegram_username == "u"
    assert domain.welcomed_at is None


def test_orm_repr_includes_token_prefix():
    orm = SpecialistORM(
        id=1,
        invite_token="abcdef1234567890",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime(2026, 5, 27, tzinfo=UTC),
    )
    assert "abcdef" in repr(orm)
