"""Background reminder pass, driven by the minute-loop in `__main__`.

The loop (sleep-until-next-minute) lives in the entry point; the work — list
candidates, run the due-gated service per specialist, deliver each message — lives
here so it is testable without a timer. A failed delivery to one client is caught
and logged; it never aborts the rest of the pass.
"""

from contextlib import suppress
from datetime import datetime
from functools import partial
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.client_audit import record_client_message
from src.bot.client_templates import template_default
from src.bot.handlers.reminders import build_reminder_keyboard
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, DeliveryStatus
from src.domain.client import Client
from src.domain.schedule import utc_to_wall
from src.domain.scheduled_message import ScheduledClientMessage
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import (
    SqlAlchemySubscriptionDeductionsRepo,
    SqlAlchemySubscriptionsRepo,
)
from src.services.consumption import (
    ConsumptionReport,
    MissedEntry,
    MissReason,
    run_consumption_if_due,
)
from src.services.digest import send_digest_if_due
from src.services.message_templates import resolve_template
from src.services.payment_reminder import (
    PaymentReminderAlert,
    run_payment_reminders_if_due,
)
from src.services.reminder import (
    ReminderMessages,
    ReminderToSend,
    run_reminders_if_due,
)
from src.services.scheduled_messages import collect_due

logger = logging.getLogger(__name__)


async def run_reminder_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One reminder pass over all enabled specialists at `now`."""
    async with session_factory() as session:
        candidates = await SqlAlchemySpecialistsRepo(session).list_reminder_candidates()
    for specialist in candidates:
        assert specialist.id is not None  # noqa: S101 — candidates are persisted
        async with session_factory() as session:
            # Each specialist may override the reminder text; resolve per pass.
            client_text = await resolve_template(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist.id,
                key="appt_reminder",
                default=messages.reminder.client_text,
            )
            to_send = await run_reminders_if_due(
                specialist,
                now,
                appointments_repo=SqlAlchemyAppointmentsRepo(session),
                reminders_repo=SqlAlchemyRemindersRepo(session),
                specialists_repo=SqlAlchemySpecialistsRepo(session),
                schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
                slot_repo=SqlAlchemyRecurringSlotRepo(session),
                override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
                clients_repo=SqlAlchemyClientsRepo(session),
                messages=ReminderMessages(client_text=client_text),
            )
        for item in to_send:
            await deliver_reminder(bot, messages, item, session_factory)


async def run_digest_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One morning-digest pass over all enabled specialists at `now`."""
    async with session_factory() as session:
        candidates = await SqlAlchemySpecialistsRepo(session).list_digest_candidates()
    for specialist in candidates:
        assert specialist.id is not None  # noqa: S101 — candidates are persisted
        async with session_factory() as session:
            try:
                await send_digest_if_due(
                    specialist,
                    now,
                    appointments_repo=SqlAlchemyAppointmentsRepo(session),
                    specialists_repo=SqlAlchemySpecialistsRepo(session),
                    schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
                    slot_repo=SqlAlchemyRecurringSlotRepo(session),
                    override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
                    clients_repo=SqlAlchemyClientsRepo(session),
                    messages=messages.digest,
                    send=bot.send_message,
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                # Specialist blocked the bot / chat gone: the day is already stamped
                # done inside the service, so we never retry-loop within the day.
                logger.warning(
                    "specialist.digest_failed",
                    extra={"specialist_id": specialist.id},
                )


async def run_payment_reminder_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One subscription-payment-reminder pass over all enabled specialists.

    For each specialist due today, the service alerts about clients whose
    subscription ran out and who have an appointment tomorrow. The alert (a
    specialist-facing message with an optional "send" button) is delivered here,
    keeping aiogram out of the service.
    """
    async with session_factory() as session:
        candidates = await SqlAlchemySpecialistsRepo(
            session
        ).list_payment_reminder_candidates()
    for specialist in candidates:
        assert specialist.id is not None  # noqa: S101 — candidates are persisted
        await _run_payment_for_specialist(
            bot, session_factory, messages, specialist, now
        )


async def _run_payment_for_specialist(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    specialist: Specialist,
    now: datetime,
) -> None:
    assert specialist.id is not None  # noqa: S101 — candidates are persisted
    async with session_factory() as session:
        # Preview is the same fixed client text for every alert of this specialist.
        preview = await resolve_template(
            SqlAlchemyMessageTemplatesRepo(session),
            specialist_id=specialist.id,
            key="payment_reminder",
            default=template_default(messages, "payment_reminder"),
        )
        await run_payment_reminders_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            subscriptions_repo=SqlAlchemySubscriptionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            alert=partial(_deliver_payment_alert, bot, messages, specialist, preview),
        )


def _payment_keyboard(messages: BotMessages, client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=messages.payment.btn_send,
                    callback_data=f"pay:send:{client_id}",
                )
            ]
        ]
    )


async def _deliver_payment_alert(
    bot: Bot,
    messages: BotMessages,
    specialist: Specialist,
    preview: str,
    alert: PaymentReminderAlert,
) -> None:
    if specialist.telegram_chat_id is None:  # pragma: no cover — candidates welcomed
        return
    wall = utc_to_wall(alert.starts_at, specialist.timezone)
    text = messages.payment.alert.format(
        child=alert.child_name, time=f"{wall:%H:%M}", preview=preview
    )
    # The "send" button only appears for a linked client — otherwise there is
    # nowhere to send, and the specialist reminds them by hand. Telling the
    # specialist may itself fail (they blocked the bot); swallow that.
    markup = (
        _payment_keyboard(messages, alert.client_id)
        if alert.chat_id is not None
        else None
    )
    with suppress(TelegramForbiddenError, TelegramBadRequest):
        await bot.send_message(specialist.telegram_chat_id, text, reply_markup=markup)


async def run_consumption_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One evening subscription-consumption pass over all enabled specialists.

    For each specialist due today, the service deducts today's passed meetings and
    accumulates a report; the report (charged subscriptions as buttons + a ❗ list of
    meetings it could not charge) is delivered here, keeping aiogram out of the
    service. A delivery failure never rolls back the deductions (they are already
    committed inside the service).
    """
    async with session_factory() as session:
        candidates = await SqlAlchemySpecialistsRepo(
            session
        ).list_consumption_candidates()
    for specialist in candidates:
        assert specialist.id is not None  # noqa: S101 — candidates are persisted
        await _run_consumption_for_specialist(
            bot, session_factory, messages, specialist, now
        )


async def _run_consumption_for_specialist(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    specialist: Specialist,
    now: datetime,
) -> None:
    async with session_factory() as session:
        await run_consumption_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            subscriptions_repo=SqlAlchemySubscriptionsRepo(session),
            deductions_repo=SqlAlchemySubscriptionDeductionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            report=partial(deliver_consumption_report, bot, messages, specialist),
        )


def render_consumption_report(report: ConsumptionReport, messages: BotMessages) -> str:
    """Report text: only the block headers — both lists are rendered as buttons.

    Charged subscriptions and ❗ uncharged meetings are buttons (see
    `_consumption_keyboard`); here we keep just the headers so the message reads
    (which blocks are present) even without tapping.
    """
    m = messages.consumption
    lines = [m.title]
    if report.deducted:
        lines.append(m.deducted_header)
    if report.missed:
        lines.append(m.missed_header)
    return "\n".join(lines)


def _missed_button(
    miss: MissedEntry, specialist: Specialist, messages: BotMessages
) -> InlineKeyboardButton:
    """One ❗-meeting button: EXHAUSTED → subscription card, else client card."""
    wall = utc_to_wall(miss.starts_at, specialist.timezone)
    if miss.reason is MissReason.EXHAUSTED:
        assert miss.subscription_id is not None  # noqa: S101 — set on EXHAUSTED
        text = messages.consumption.missed_exhausted
        callback_data = f"subs:card:{miss.subscription_id}"
    else:
        text = messages.consumption.missed_no_subscription
        callback_data = f"clients:card:{miss.client_id}"
    return InlineKeyboardButton(
        text=text.format(child=miss.child_name, time=f"{wall:%H:%M}"),
        callback_data=callback_data,
    )


def _consumption_keyboard(
    report: ConsumptionReport, specialist: Specialist, messages: BotMessages
) -> InlineKeyboardMarkup | None:
    if report.is_empty:
        return None
    rows = [
        [
            InlineKeyboardButton(
                text=messages.consumption.deducted_btn.format(
                    child=entry.child_name, remaining=entry.remaining
                ),
                callback_data=f"subs:card:{entry.subscription_id}",
            )
        ]
        for entry in report.deducted
    ]
    rows.extend([_missed_button(miss, specialist, messages)] for miss in report.missed)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def deliver_consumption_report(
    bot: Bot, messages: BotMessages, specialist: Specialist, report: ConsumptionReport
) -> None:
    """Send the consumption report to the specialist (shared with "send now").

    A delivery failure is swallowed — the deductions are already committed and must
    not be rolled back by a messaging error.
    """
    if specialist.telegram_chat_id is None:  # pragma: no cover — candidates welcomed
        return
    text = render_consumption_report(report, messages)
    markup = _consumption_keyboard(report, specialist, messages)
    # Telling the specialist may itself fail (they blocked the bot); swallow it —
    # the deductions are already committed and must not be rolled back.
    with suppress(TelegramForbiddenError, TelegramBadRequest):
        await bot.send_message(specialist.telegram_chat_id, text, reply_markup=markup)


async def run_outbox_pass(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    messages: BotMessages,
    now: datetime,
) -> None:
    """One delivery pass over deferred client notifications due at `now`.

    Catches up overdue rows after downtime (any queued row with `due_at <= now`).
    Each send is independent: a delivery failure marks that row failed and notifies
    the specialist, but never aborts the rest of the pass.
    """
    async with session_factory() as session:
        due = await collect_due(SqlAlchemyScheduledMessagesRepo(session), now)
    for message in due:
        await _deliver_outbox(bot, messages, message, session_factory, now)


async def _deliver_outbox(
    bot: Bot,
    messages: BotMessages,
    message: ScheduledClientMessage,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime,
) -> None:
    assert message.id is not None  # noqa: S101 — due rows are persisted
    async with session_factory() as session:
        client = await SqlAlchemyClientsRepo(session).get_for_specialist(
            message.client_id, message.specialist_id
        )
    # Re-check the link: a client unlinked since enqueue has nothing to receive the
    # message, so we leave the row queued (visible/cancellable on the card).
    if client is None or client.telegram_chat_id is None:
        return
    extra = {"specialist_id": message.specialist_id, "client_id": message.client_id}
    try:
        await bot.send_message(message.chat_id, message.text)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        await _outbox_failed(
            bot, messages, message, session_factory, now=now, client=client, exc=exc
        )
        return
    async with session_factory() as session:
        await SqlAlchemyScheduledMessagesRepo(session).mark_sent(message.id, now)
    logger.info("appointment.notify_deferred_sent", extra=extra)
    await record_client_message(
        session_factory,
        specialist_id=message.specialist_id,
        client_id=message.client_id,
        event=message.event,
        text=message.text,
        status=DeliveryStatus.SENT,
    )


async def _outbox_failed(  # noqa: PLR0913
    bot: Bot,
    messages: BotMessages,
    message: ScheduledClientMessage,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    client: Client,
    exc: Exception,
) -> None:
    assert message.id is not None  # noqa: S101 — due rows are persisted
    extra = {"specialist_id": message.specialist_id, "client_id": message.client_id}
    async with session_factory() as session:
        await SqlAlchemyScheduledMessagesRepo(session).mark_failed(message.id, now)
    logger.warning("appointment.notify_failed", extra=extra)
    await record_client_message(
        session_factory,
        specialist_id=message.specialist_id,
        client_id=message.client_id,
        event=message.event,
        text=message.text,
        status=DeliveryStatus.FAILED,
        error=str(exc),
    )
    await _notify_specialist_failure(bot, messages, message, session_factory, client)


async def _notify_specialist_failure(
    bot: Bot,
    messages: BotMessages,
    message: ScheduledClientMessage,
    session_factory: async_sessionmaker[AsyncSession],
    client: Client,
) -> None:
    # Unlike the immediate path, the specialist is not in the chat — tell them the
    # deferred delivery failed. This message may itself fail (they blocked the bot);
    # swallow that — the journal already holds the fact.
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(message.specialist_id)
    if specialist is None or specialist.telegram_chat_id is None:  # pragma: no cover
        return
    text = messages.schedule.notify_deferred_failed.format(child=client.child_name)
    with suppress(TelegramForbiddenError, TelegramBadRequest):
        await bot.send_message(specialist.telegram_chat_id, text)


async def deliver_reminder(
    bot: Bot,
    messages: BotMessages,
    item: ReminderToSend,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Send one reminder to a client and journal it (SENT/FAILED).

    Shared by the scheduled pass and the manual "send reminders now" settings
    action so both deliver and audit through the same funnel.
    """
    extra = {"specialist_id": item.specialist_id, "client_id": item.client_id}
    try:
        await bot.send_message(
            item.chat_id,
            item.text,
            reply_markup=build_reminder_keyboard(item.reminder_id, messages.reminder),
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        # Client blocked the bot / chat unavailable: the journal row already exists
        # so we never retry-loop; the day stays marked done.
        logger.warning("appointment.reminder_failed", extra=extra)
        await record_client_message(
            session_factory,
            specialist_id=item.specialist_id,
            client_id=item.client_id,
            event=AuditEvent.REMINDER,
            text=item.text,
            status=DeliveryStatus.FAILED,
            error=str(exc),
        )
        return
    logger.info("appointment.reminder_sent", extra=extra)
    await record_client_message(
        session_factory,
        specialist_id=item.specialist_id,
        client_id=item.client_id,
        event=AuditEvent.REMINDER,
        text=item.text,
        status=DeliveryStatus.SENT,
    )
