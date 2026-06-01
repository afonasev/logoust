import asyncio

from aiogram import Bot

from src.bot.dispatcher import build_dispatcher
from src.bot.messages import DEFAULT_MESSAGES_PATH, load_messages
from src.config import settings
from src.infrastructure.db import build_engine, build_session_factory
from src.logging_setup import setup_logging


async def main() -> None:
    setup_logging()
    messages = load_messages(DEFAULT_MESSAGES_PATH)
    engine = build_engine(settings.DATABASE_URL)
    session_factory = build_session_factory(engine)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    dp = build_dispatcher(messages, session_factory)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
