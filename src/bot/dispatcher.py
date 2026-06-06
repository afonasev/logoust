from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import build_router as build_clients_router
from src.bot.handlers.reminders import build_router as build_reminders_router
from src.bot.handlers.schedule import build_router as build_schedule_router
from src.bot.handlers.settings import build_router as build_settings_router
from src.bot.handlers.start import build_router as build_start_router
from src.bot.handlers.subscriptions import build_router as build_subscriptions_router
from src.bot.handlers.windows import build_router as build_windows_router
from src.bot.messages import BotMessages


def build_dispatcher(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dp = Dispatcher()
    # Feature routers must precede start: start's fallback handler matches any text
    # (pasted-token onboarding), so it would otherwise swallow the reply-keyboard
    # buttons and every wizard input. Unmatched text still falls through to start.
    dp.include_router(build_clients_router(messages, session_factory))
    # Client-facing callback router (no specialist middleware): register it before
    # start so the fallback text handler never shadows it.
    dp.include_router(build_reminders_router(messages, session_factory))
    dp.include_router(build_schedule_router(messages, session_factory))
    dp.include_router(build_settings_router(messages, session_factory))
    dp.include_router(build_subscriptions_router(messages, session_factory))
    dp.include_router(build_windows_router(messages, session_factory))
    dp.include_router(build_start_router(messages, session_factory))
    return dp
