import contextlib
import dataclasses
from unittest.mock import AsyncMock

from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.keyboard_refresh import KeyboardRefreshMiddleware, keyboard_version
from src.bot.messages import BotMessages
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import consume_invite, create_invite


def _rename_button(messages: BotMessages) -> BotMessages:
    clients = dataclasses.replace(messages.clients, button="ИЗМЕНЁННАЯ КНОПКА")
    return dataclasses.replace(messages, clients=clients)


def test_keyboard_version_stable_without_changes(messages: BotMessages):
    assert keyboard_version(messages) == keyboard_version(messages)


def test_keyboard_version_changes_when_button_renamed(messages: BotMessages):
    assert keyboard_version(messages) != keyboard_version(_rename_button(messages))


async def _welcomed_specialist(
    session_factory: async_sessionmaker[AsyncSession], *, chat_id: int
) -> int:
    async with session_factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        specialist = await create_invite(repo)
        await consume_invite(
            repo, specialist.invite_token, chat_id=chat_id, username=None
        )
        assert specialist.id is not None
        return specialist.id


async def test_stale_version_injects_markup_and_records_version(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=100)
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id=100, text="hello")
    make_request = AsyncMock(return_value="ok")

    await mw(make_request, AsyncMock(), method)

    # The middleware mutates the method in place before sending it.
    assert isinstance(method.reply_markup, ReplyKeyboardMarkup)
    async with session_factory() as session:
        refreshed = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert refreshed is not None
    assert refreshed.keyboard_version == keyboard_version(messages)


async def test_current_version_leaves_markup_and_version_untouched(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=101)
    version = keyboard_version(messages)
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).set_keyboard_version(
            specialist_id, version
        )
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id=101, text="hello")
    make_request = AsyncMock(return_value="ok")

    await mw(make_request, AsyncMock(), method)

    assert method.reply_markup is None


async def test_delivery_failure_does_not_record_version(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=102)
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id=102, text="hello")
    make_request = AsyncMock(side_effect=RuntimeError("delivery failed"))

    with contextlib.suppress(RuntimeError):
        await mw(make_request, AsyncMock(), method)

    async with session_factory() as session:
        refreshed = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert refreshed is not None
    assert refreshed.keyboard_version is None


async def test_non_send_message_method_is_passed_through(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    await _welcomed_specialist(session_factory, chat_id=103)
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = EditMessageText(chat_id=103, message_id=1, text="edit")
    make_request = AsyncMock(return_value="ok")

    await mw(make_request, AsyncMock(), method)

    make_request.assert_awaited_once()


async def test_username_chat_id_is_passed_through(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    # chat_id can be a @username string; we can't match a specialist by it.
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id="@someone", text="hi")
    make_request = AsyncMock(return_value="ok")

    await mw(make_request, AsyncMock(), method)

    assert method.reply_markup is None


async def test_unknown_chat_is_passed_through(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    mw = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id=999, text="hi")
    make_request = AsyncMock(return_value="ok")

    await mw(make_request, AsyncMock(), method)

    assert method.reply_markup is None
