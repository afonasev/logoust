import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.dispatcher import build_dispatcher
from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.bot.scheduler import (
    run_consumption_pass,
    run_digest_pass,
    run_outbox_pass,
    run_payment_reminder_pass,
    run_reminder_pass,
)
from src.config import settings
from src.infrastructure.db import build_engine, build_session_factory
from src.logging_setup import setup_logging

logger = logging.getLogger(__name__)

# One scheduler tick per minute: cheap, and per-specialist wall-clock times only
# need minute resolution. The loop sleeps to the next minute boundary so it does
# not drift, and the whole pass is guarded so a failure never kills polling.


async def _sleep_to_next_minute() -> None:
    now = datetime.now(UTC)
    nxt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    await asyncio.sleep((nxt - now).total_seconds())


async def _scheduler_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
) -> None:
    while True:
        await _sleep_to_next_minute()
        now = datetime.now(UTC)
        # Each pass is independently guarded so a failure in one never skips the
        # other and never kills the loop (and thus polling).
        try:
            await run_reminder_pass(bot, session_factory, messages, now)
        except Exception:
            logger.exception("scheduler.pass_failed")
        try:
            await run_digest_pass(bot, session_factory, messages, now)
        except Exception:
            logger.exception("scheduler.digest_pass_failed")
        try:
            await run_payment_reminder_pass(bot, session_factory, messages, now)
        except Exception:
            logger.exception("scheduler.payment_reminder_pass_failed")
        try:
            await run_consumption_pass(bot, session_factory, messages, now)
        except Exception:
            logger.exception("scheduler.consumption_pass_failed")
        try:
            await run_outbox_pass(bot, session_factory, messages, now)
        except Exception:
            logger.exception("scheduler.outbox_pass_failed")


async def main() -> None:
    setup_logging()
    messages = load_messages(DEFAULT_MESSAGES_PATH)
    engine = build_engine(settings.DATABASE_URL)
    session_factory = build_session_factory(engine)
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    dp = build_dispatcher(messages, session_factory)
    # The minute-scheduler is a background task tied to polling's lifetime, not a
    # gather peer: aiogram's start_polling handles SIGINT/SIGTERM and *returns* on
    # shutdown, so gathering it with the never-ending loop would hang on Ctrl-C
    # waiting for the loop. Cancelling the task on exit lets the process stop cleanly.
    scheduler_task = asyncio.create_task(
        _scheduler_loop(bot, session_factory, messages)
    )
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
