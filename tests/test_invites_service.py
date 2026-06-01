from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import ConsumeResult, consume_invite, create_invite


async def test_create_invite_persists_unique_record(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    a = await create_invite(repo)
    b = await create_invite(repo)
    assert a.id != b.id
    assert a.invite_token != b.invite_token
    assert a.welcomed_at is None
    assert a.telegram_chat_id is None


async def test_consume_invite_unknown_token(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    result = await consume_invite(repo, "nope", chat_id=1, username="x")
    assert result is ConsumeResult.UNKNOWN_TOKEN


async def test_consume_invite_first_time_welcomes(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)
    result = await consume_invite(
        repo, specialist.invite_token, chat_id=100, username="ivanov"
    )
    assert result is ConsumeResult.WELCOMED
    found = await repo.find_by_token(specialist.invite_token)
    assert found is not None
    assert found.telegram_chat_id == 100
    assert found.telegram_username == "ivanov"
    assert found.welcomed_at is not None


async def test_consume_invite_idempotent_on_replay(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)
    await consume_invite(repo, specialist.invite_token, chat_id=100, username=None)
    before = await repo.find_by_token(specialist.invite_token)
    assert before is not None
    welcomed_at_first = before.welcomed_at

    result = await consume_invite(
        repo, specialist.invite_token, chat_id=200, username="other"
    )
    assert result is ConsumeResult.ALREADY_WELCOMED
    after = await repo.find_by_token(specialist.invite_token)
    assert after is not None
    assert after.telegram_chat_id == 100
    assert after.telegram_username is None
    assert after.welcomed_at == welcomed_at_first


async def test_consume_invite_logs_chat_conflict(
    session: AsyncSession,
    caplog,
):
    repo = SqlAlchemySpecialistsRepo(session)
    first = await create_invite(repo)
    await consume_invite(repo, first.invite_token, chat_id=42, username="a")

    second = await create_invite(repo)
    with caplog.at_level("WARNING"):
        result = await consume_invite(
            repo, second.invite_token, chat_id=42, username="b"
        )

    assert result is ConsumeResult.ALREADY_WELCOMED
    assert any(r.message == "specialist.invite_chat_conflict" for r in caplog.records)
    after_second = await repo.find_by_token(second.invite_token)
    assert after_second is not None
    assert after_second.welcomed_at is None
    assert after_second.telegram_chat_id is None


async def test_consume_invite_handles_username_none(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)
    result = await consume_invite(
        repo, specialist.invite_token, chat_id=55, username=None
    )
    assert result is ConsumeResult.WELCOMED
    found = await repo.find_by_token(specialist.invite_token)
    assert found is not None
    assert found.telegram_username is None
    assert isinstance(found.welcomed_at, datetime)
    assert found.welcomed_at.tzinfo is None or found.welcomed_at <= datetime.now(UTC)
