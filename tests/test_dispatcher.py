from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.dispatcher import build_dispatcher
from src.bot.messages import BotMessages


def test_build_dispatcher_includes_start_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
):
    dp = build_dispatcher(messages, session_factory)
    assert isinstance(dp, Dispatcher)
    sub_routers = [r for r in dp.sub_routers if r.name == "start"]
    assert len(sub_routers) == 1
