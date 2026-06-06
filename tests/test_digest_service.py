from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import DEFAULT_MESSAGES_PATH, load_messages
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.digest import send_digest_if_due
from src.services.invites import create_invite

_TZ = "Asia/Yekaterinburg"
_SP = 1
_NOW = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)  # 11:00 wall — past the 10:00 default
_TODAY = date(2026, 6, 15)

_DIGEST = load_messages(DEFAULT_MESSAGES_PATH).digest


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        await create_invite(repo)
        # Welcomed so the digest has a chat to go to.
        await repo.mark_welcomed(
            _SP, telegram_chat_id=999, telegram_username=None, welcomed_at=_NOW
        )


async def _seed_client(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=_SP,
                child_name="Петя",
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
    assert client.id is not None
    return client.id


async def _seed_appointment(
    factory: async_sessionmaker[AsyncSession], client_id: int, hhmm: str
) -> None:
    async with factory() as session:
        await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=_TODAY,
            hhmm=hhmm,
            comment="первое занятие",
            tz=_TZ,
            now=_NOW,
        )


async def _load_specialist(factory: async_sessionmaker[AsyncSession]):
    async with factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert specialist is not None
    return specialist


async def _run(
    factory: async_sessionmaker[AsyncSession], specialist, now, send
) -> bool:
    async with factory() as session:
        return await send_digest_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            recurring_repo=SqlAlchemyRecurringRepo(session),
            exceptions_repo=SqlAlchemyRecurringExceptionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            messages=_DIGEST,
            send=send,
        )


async def test_non_empty_day_sends_and_marks(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id, "10:00")
    specialist = await _load_specialist(session_factory)
    send = AsyncMock()

    sent = await _run(session_factory, specialist, _NOW, send)

    assert sent is True
    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.args[0] == 999
    assert "Петя" in call.args[1]
    assert "первое занятие" in call.args[1]
    assert (
        await _load_specialist(session_factory)
    ).morning_notify_last_run_on == _TODAY


async def test_empty_day_marks_without_sending(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    specialist = await _load_specialist(session_factory)
    send = AsyncMock()

    sent = await _run(session_factory, specialist, _NOW, send)

    assert sent is False
    send.assert_not_awaited()
    # The empty day is still marked so a later appointment does not trigger a digest.
    assert (
        await _load_specialist(session_factory)
    ).morning_notify_last_run_on == _TODAY


async def test_not_due_does_nothing(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id, "10:00")
    specialist = await _load_specialist(session_factory)
    send = AsyncMock()
    # 04:00 UTC → 09:00 wall, before the 10:00 trigger.
    before = datetime(2026, 6, 15, 4, 0, tzinfo=UTC)

    sent = await _run(session_factory, specialist, before, send)

    assert sent is False
    send.assert_not_awaited()
    assert (await _load_specialist(session_factory)).morning_notify_last_run_on is None


async def test_send_failure_still_marks_day(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id, "10:00")
    specialist = await _load_specialist(session_factory)
    send = AsyncMock(side_effect=RuntimeError("boom"))

    # The caller is responsible for catching delivery errors; the day is stamped
    # before the send so a failure never re-triggers the pass.
    with pytest.raises(RuntimeError, match="boom"):
        await _run(session_factory, specialist, _NOW, send)

    assert (
        await _load_specialist(session_factory)
    ).morning_notify_last_run_on == _TODAY
