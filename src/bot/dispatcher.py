from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.start import build_router
from src.bot.messages import BotMessages


def build_dispatcher(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(build_router(messages, session_factory))
    return dp
