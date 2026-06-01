from collections.abc import Awaitable, Callable
import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import BotMessages
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import ConsumeResult, consume_invite

logger = logging.getLogger(__name__)

StartHandler = Callable[[Message, CommandObject], Awaitable[None]]


def make_start_handler(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> StartHandler:
    async def handle_start(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return

        token = command.args
        if not token:
            await message.answer(messages.start.no_token)
            return

        async with session_factory() as session:
            repo = SqlAlchemySpecialistsRepo(session)
            result = await consume_invite(
                repo,
                token,
                chat_id=message.from_user.id,
                username=message.from_user.username,
            )

        if result is ConsumeResult.WELCOMED:
            await message.answer(messages.start.welcome)
        elif result is ConsumeResult.ALREADY_WELCOMED:
            await message.answer(messages.start.already_welcomed)
        else:
            await message.answer(messages.start.unknown_token)

    return handle_start


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="start")
    handler = make_start_handler(messages, session_factory)
    router.message.register(handler, CommandStart())
    return router
