from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo

_SP = 1
_KEY = "appt_reminder"


async def test_get_returns_none_when_absent(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    assert await repo.get(_SP, _KEY) is None


async def test_upsert_inserts_then_replaces(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    await repo.upsert(_SP, _KEY, "первый")
    stored = await repo.get(_SP, _KEY)
    assert stored is not None
    assert stored.body == "первый"
    # A second upsert for the same key replaces, not duplicates.
    await repo.upsert(_SP, _KEY, "второй")
    replaced = await repo.get(_SP, _KEY)
    assert replaced is not None
    assert replaced.body == "второй"


async def test_delete_removes_override(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    await repo.upsert(_SP, _KEY, "текст")
    assert await repo.delete(_SP, _KEY) is True
    assert await repo.get(_SP, _KEY) is None


async def test_delete_returns_false_when_nothing_to_delete(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    assert await repo.delete(_SP, _KEY) is False


async def test_overrides_are_isolated_by_specialist(session: AsyncSession):
    repo = SqlAlchemyMessageTemplatesRepo(session)
    await repo.upsert(_SP, _KEY, "для первого")
    assert await repo.get(2, _KEY) is None
