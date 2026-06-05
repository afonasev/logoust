from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.bot.scheduler import run_reminder_pass
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

_TZ = "Asia/Yekaterinburg"
_SP = 1
_NOW = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)  # 13:00 wall — past the 12:00 default
_TOMORROW = date(2026, 6, 16)


def _messages() -> BotMessages:
    return load_messages(DEFAULT_MESSAGES_PATH)


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_linked_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int, child: str = "Петя"
) -> int:
    async with factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=_SP,
                child_name=child,
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
        assert client.id is not None
        await repo.link_telegram(
            client.id,
            telegram_chat_id=chat_id,
            username=None,
            linked_at=_NOW,
            updated_at=_NOW,
        )
    return client.id


async def _seed_appointment(
    factory: async_sessionmaker[AsyncSession], client_id: int, hhmm: str
) -> None:
    async with factory() as session:
        await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=_TOMORROW,
            hhmm=hhmm,
            comment=None,
            tz=_TZ,
            now=_NOW,
        )


async def test_pass_sends_to_due_client(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 555
    # Journal row created so a second pass would not re-send (10:00 wall = 05:00 UTC).
    starts_at = datetime(2026, 6, 16, 5, 0, tzinfo=UTC)
    async with session_factory() as session:
        statuses = await SqlAlchemyRemindersRepo(session).statuses_for_day(
            _SP, [(client_id, starts_at)]
        )
    assert statuses


async def test_pass_skips_non_due_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    # Before noon wall (05:00 UTC → 10:00 wall): not due.
    bot = AsyncMock()
    await run_reminder_pass(
        bot, session_factory, _messages(), datetime(2026, 6, 15, 5, 0, tzinfo=UTC)
    )
    bot.send_message.assert_not_awaited()


async def test_delivery_failure_does_not_stop_pass(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    a = await _seed_linked_client(session_factory, chat_id=111, child="Аня")
    b = await _seed_linked_client(session_factory, chat_id=222, child="Боря")
    await _seed_appointment(session_factory, a, "10:00")
    await _seed_appointment(session_factory, b, "11:00")
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramForbiddenError(method=None, message="blocked"),  # type: ignore[arg-type]
        None,
    ]
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    # Both clients were attempted despite the first one failing.
    assert bot.send_message.await_count == 2
