from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.reminders import (
    ReminderHandlers,
    build_confirm_callback,
    parse_confirm_callback,
)
from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.schedule import wall_to_utc
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

_TZ = "Asia/Yekaterinburg"
_SP = 1
_CHAT = 555
_SPEC_CHAT = 900
_NOW = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
_TOMORROW = date(2026, 6, 16)
_STARTS = wall_to_utc(_TOMORROW, "10:00", _TZ)


def test_build_and_parse_roundtrip():
    assert build_confirm_callback(7, confirm=True) == "appt:cfm:7:y"
    assert build_confirm_callback(7, confirm=False) == "appt:cfm:7:n"
    assert parse_confirm_callback("appt:cfm:7:y") == (7, True)
    assert parse_confirm_callback("appt:cfm:7:n") == (7, False)


@pytest.mark.parametrize(
    "data", [None, "other:1:y", "appt:cfm:x:y", "appt:cfm:7:z", "appt:cfm:7"]
)
def test_parse_rejects_bad_callback(data: str | None):
    assert parse_confirm_callback(data) is None


def _messages() -> BotMessages:
    return load_messages(DEFAULT_MESSAGES_PATH)


def _fake_callback(data: str, *, from_id: int) -> AsyncMock:
    cb = AsyncMock()
    cb.data = data
    cb.from_user = AsyncMock()
    cb.from_user.id = from_id
    cb.answer = AsyncMock()
    cb.bot = AsyncMock()
    cb.bot.send_message = AsyncMock()
    return cb


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        await create_invite(repo)
        await repo.mark_welcomed(
            _SP, telegram_chat_id=_SPEC_CHAT, telegram_username=None, welcomed_at=_NOW
        )


async def _seed_client(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=_SP,
                child_name="Петя",
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
        assert client.id is not None
        await repo.link_telegram(
            client.id,
            telegram_chat_id=_CHAT,
            username=None,
            linked_at=_NOW,
            updated_at=_NOW,
        )
    return client.id


async def _seed_reminder(
    factory: async_sessionmaker[AsyncSession],
    client_id: int,
    *,
    slot_id: int | None = None,
    origin_date: date | None = None,
) -> int:
    async with factory() as session:
        reminder = AppointmentReminder(
            id=None,
            specialist_id=_SP,
            client_id=client_id,
            starts_at=_STARTS,
            slot_id=slot_id,
            origin_date=origin_date,
            status=ReminderStatus.PENDING,
            sent_at=_NOW,
            responded_at=None,
        )
        await SqlAlchemyRemindersRepo(session).insert_pending(reminder)
    assert reminder.id is not None
    return reminder.id


def _handlers(factory: async_sessionmaker[AsyncSession]) -> ReminderHandlers:
    return ReminderHandlers(_messages(), factory)


async def test_confirm_toasts_without_notifying(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    cb = _fake_callback(
        build_confirm_callback(reminder_id, confirm=True), from_id=_CHAT
    )
    await _handlers(session_factory).confirm(cb)
    cb.answer.assert_awaited_once_with(_messages().reminder.confirmed_toast)
    cb.bot.send_message.assert_not_awaited()


async def test_decline_one_off_notifies_with_card_button(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    async with session_factory() as session:
        appt = await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=_TOMORROW,
            hhmm="10:00",
            comment=None,
            tz=_TZ,
            now=_NOW,
        )
    reminder_id = await _seed_reminder(session_factory, client_id)
    cb = _fake_callback(
        build_confirm_callback(reminder_id, confirm=False), from_id=_CHAT
    )
    await _handlers(session_factory).confirm(cb)
    cb.answer.assert_awaited_once_with(_messages().reminder.declined_toast)
    cb.bot.send_message.assert_awaited_once()
    assert cb.bot.send_message.await_args.args[0] == _SPEC_CHAT
    markup = cb.bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"sched:card:{appt.id}"


async def test_decline_one_off_without_row_falls_back_to_day_view(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    cb = _fake_callback(
        build_confirm_callback(reminder_id, confirm=False), from_id=_CHAT
    )
    await _handlers(session_factory).confirm(cb)
    markup = cb.bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "sched:day_view:2026-06-16"


async def test_decline_slot_repeat_links_to_meeting_card(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(
        session_factory, client_id, slot_id=4, origin_date=_TOMORROW
    )
    cb = _fake_callback(
        build_confirm_callback(reminder_id, confirm=False), from_id=_CHAT
    )
    await _handlers(session_factory).confirm(cb)
    markup = cb.bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "recur:occ:4:2026-06-16"


async def test_foreign_chat_silently_dismissed(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    cb = _fake_callback(build_confirm_callback(reminder_id, confirm=True), from_id=999)
    await _handlers(session_factory).confirm(cb)
    cb.answer.assert_awaited_once_with()
    cb.bot.send_message.assert_not_awaited()
