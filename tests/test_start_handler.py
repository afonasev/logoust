from dataclasses import dataclass
from unittest.mock import AsyncMock

from aiogram.filters import CommandObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.start import build_router, make_start_handler
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
    assert len(router.message.handlers) == 1
