import dataclasses
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
import pytest
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

    assert make_request.await_args is not None
    sent = make_request.await_args.args[1]
    assert isinstance(sent.reply_markup, ReplyKeyboardMarkup)
    assert method.reply_markup is None
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

    with pytest.raises(RuntimeError, match="delivery failed"):
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


async def test_inline_response_defers_refresh_until_compatible_message(
    messages, session_factory
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=104)
    middleware = KeyboardRefreshMiddleware(messages, session_factory)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open", callback_data="open")]]
    )
    method = SendMessage(chat_id=104, text="clients", reply_markup=markup)
    request = AsyncMock(return_value="ok")
    await middleware(request, AsyncMock(), method)
    assert request.await_count == 1
    assert request.await_args is not None
    assert request.await_args.args[1] is method
    assert method.reply_markup == markup
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert specialist is not None
    assert specialist.keyboard_version is None

    await middleware(request, AsyncMock(), SendMessage(chat_id=104, text="next"))
    assert request.await_count == 2
    assert request.await_args is not None
    assert isinstance(request.await_args.args[1].reply_markup, ReplyKeyboardMarkup)
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert specialist is not None
    assert specialist.keyboard_version == keyboard_version(messages)


async def test_retry_same_method_records_successful_keyboard_delivery(
    messages, session_factory
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=105)
    middleware = KeyboardRefreshMiddleware(messages, session_factory)
    method = SendMessage(chat_id=105, text="hello")
    request = AsyncMock(side_effect=[RuntimeError("failed"), "ok"])
    with pytest.raises(RuntimeError, match="failed"):
        await middleware(request, AsyncMock(), method)
    await middleware(request, AsyncMock(), method)
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert specialist is not None
    assert specialist.keyboard_version == keyboard_version(messages)
    assert request.await_count == 2


async def test_aiogram_session_delivers_keyboard_and_records_version(
    messages, session_factory, monkeypatch
):
    specialist_id = await _welcomed_specialist(session_factory, chat_id=106)
    bot = Bot(token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    request = AsyncMock(return_value="ok")
    monkeypatch.setattr(bot.session, "make_request", request)
    bot.session.middleware(KeyboardRefreshMiddleware(messages, session_factory))
    try:
        await bot.send_message(chat_id=106, text="hello")
    finally:
        await bot.session.close()
    assert request.await_count == 1
    assert request.await_args is not None
    assert isinstance(request.await_args.args[1].reply_markup, ReplyKeyboardMarkup)
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
    assert specialist is not None
    assert specialist.keyboard_version == keyboard_version(messages)
