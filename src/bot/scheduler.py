"""Background reminder pass, driven by the minute-loop in `__main__`.

The loop (sleep-until-next-minute) lives in the entry point; the work — list
candidates, run the due-gated service per specialist, deliver each message — lives
here so it is testable without a timer. A failed delivery to one client is caught
and logged; it never aborts the rest of the pass.
"""

from datetime import datetime
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.reminders import build_reminder_keyboard
from src.bot.messages import BotMessages
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.reminder import (
    ReminderMessages,
    ReminderToSend,
    run_reminders_if_due,
)

logger = logging.getLogger(__name__)


def _reminder_messages(messages: BotMessages) -> ReminderMessages:
    return ReminderMessages(client_text=messages.reminder.client_text)


async def run_reminder_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One reminder pass over all enabled specialists at `now`."""
    async with session_factory() as session:
        candidates = await SqlAlchemySpecialistsRepo(session).list_reminder_candidates()
    reminder_messages = _reminder_messages(messages)
    for specialist in candidates:
        async with session_factory() as session:
            to_send = await run_reminders_if_due(
                specialist,
                now,
                appointments_repo=SqlAlchemyAppointmentsRepo(session),
                reminders_repo=SqlAlchemyRemindersRepo(session),
                specialists_repo=SqlAlchemySpecialistsRepo(session),
                recurring_repo=SqlAlchemyRecurringRepo(session),
                exceptions_repo=SqlAlchemyRecurringExceptionsRepo(session),
                clients_repo=SqlAlchemyClientsRepo(session),
                messages=reminder_messages,
            )
        for item in to_send:
            await _deliver(bot, messages, item)


async def _deliver(bot: Bot, messages: BotMessages, item: ReminderToSend) -> None:
    extra = {"specialist_id": item.specialist_id, "client_id": item.client_id}
    try:
        await bot.send_message(
            item.chat_id,
            item.text,
            reply_markup=build_reminder_keyboard(item.reminder_id, messages.reminder),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        # Client blocked the bot / chat unavailable: the journal row already exists
        # so we never retry-loop; the day stays marked done.
        logger.warning("appointment.reminder_failed", extra=extra)
        return
    logger.info("appointment.reminder_sent", extra=extra)
