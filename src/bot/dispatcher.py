from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import build_router as build_clients_router
from src.bot.handlers.start import build_router as build_start_router
from src.bot.messages import BotMessages


def build_dispatcher(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dp = Dispatcher()
    # Clients router must precede start: start's fallback handler matches any text
    # (pasted-token onboarding), so it would otherwise swallow the reply-keyboard
    # button and every wizard input. Unmatched text still falls through to start.
    dp.include_router(build_clients_router(messages, session_factory))
    dp.include_router(build_start_router(messages, session_factory))
    return dp
