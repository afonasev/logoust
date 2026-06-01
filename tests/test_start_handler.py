from dataclasses import dataclass
from unittest.mock import AsyncMock

from aiogram.filters import CommandObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.start import (
    build_router,
    extract_token,
    make_start_handler,
    make_token_handler,
)
from src.bot.messages import BotMessages
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import create_invite


@dataclass(slots=True)
class FakeUser:
    id: int
    username: str | None = None


_DEFAULT_USER = FakeUser(id=42, username="ivanov")


def _fake_message(*, user: FakeUser | None = _DEFAULT_USER) -> AsyncMock:
    msg = AsyncMock()
    msg.from_user = user
    msg.answer = AsyncMock()
    return msg


def _cmd(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="start", args=args)


async def test_no_token_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(None))
    msg.answer.assert_awaited_once_with(messages.start.no_token)


async def test_unknown_token_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd("nope"))
    msg.answer.assert_awaited_once_with(messages.start.unknown_token)


async def test_welcomed_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_start_handler(messages, session_factory)
    msg = _fake_message()
    await handler(msg, _cmd(specialist.invite_token))
    msg.answer.assert_awaited_once_with(messages.start.welcome)


async def test_already_welcomed_branch(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_start_handler(messages, session_factory)
    first = _fake_message()
    await handler(first, _cmd(specialist.invite_token))
    first.answer.assert_awaited_once_with(messages.start.welcome)

    second = _fake_message()
    await handler(second, _cmd(specialist.invite_token))
    second.answer.assert_awaited_once_with(messages.start.already_welcomed)


async def test_handler_skips_when_no_from_user(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_start_handler(messages, session_factory)
    msg = _fake_message(user=None)
    await handler(msg, _cmd("anything"))
    msg.answer.assert_not_awaited()


def test_build_router_registers_start_handler(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    router = build_router(messages, session_factory)
    assert router.name == "start"
    assert len(router.message.handlers) == 2


def test_extract_token_from_bare_code():
    assert extract_token("tqHQOW3p8fPsmbEN3xDoIg") == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_from_deep_link():
    link = "https://t.me/logoust_test?start=tqHQOW3p8fPsmbEN3xDoIg"
    assert extract_token(link) == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_strips_surrounding_whitespace():
    assert extract_token("  tqHQOW3p8fPsmbEN3xDoIg\n") == "tqHQOW3p8fPsmbEN3xDoIg"


def test_extract_token_rejects_plain_message():
    assert extract_token("привет, как дела?") is None


def test_extract_token_rejects_wrong_length():
    assert extract_token("short") is None


async def test_token_handler_welcomes_on_pasted_code(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = specialist.invite_token
    await handler(msg)
    msg.answer.assert_awaited_once_with(messages.start.welcome)


async def test_token_handler_welcomes_on_pasted_link(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)

    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = f"https://t.me/logoust_test?start={specialist.invite_token}"
    await handler(msg)
    msg.answer.assert_awaited_once_with(messages.start.welcome)


async def test_token_handler_ignores_plain_message(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = "привет"
    await handler(msg)
    msg.answer.assert_not_awaited()


async def test_token_handler_skips_when_no_from_user(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message(user=None)
    msg.text = "tqHQOW3p8fPsmbEN3xDoIg"
    await handler(msg)
    msg.answer.assert_not_awaited()


async def test_token_handler_skips_when_no_text(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    handler = make_token_handler(messages, session_factory)
    msg = _fake_message()
    msg.text = None
    await handler(msg)
    msg.answer.assert_not_awaited()
