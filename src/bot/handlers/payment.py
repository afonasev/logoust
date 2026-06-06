"""Specialist-tap delivery of the subscription payment reminder to a client.

The scheduler posts the specialist an alert with a "send" button carrying
`pay:send:<client_id>`; tapping it sends the fixed renewal-request template to
the client and journals the outcome through the client-audit funnel.
"""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.client_audit import record_client_message
from src.bot.client_templates import template_default
from src.bot.handlers.clients import SpecialistMiddleware
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, DeliveryStatus
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.services.message_templates import resolve_template

logger = logging.getLogger(__name__)

_CB_SEND = "pay:send:"  # + client_id


class PaymentHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.payment
        self._messages = messages
        self._session_factory = session_factory

    async def send_payment_reminder(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        client_id = int((callback.data or "").removeprefix(_CB_SEND))
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        # Re-check the link at tap time: the button can live arbitrarily long, so
        # the client may have unlinked since the alert was posted.
        if client is None or client.telegram_chat_id is None:
            await callback.answer(self._m.not_linked, show_alert=True)
            return
        async with self._session_factory() as session:
            text = await resolve_template(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist_id,
                key="payment_reminder",
                default=template_default(self._messages, "payment_reminder"),
            )
        assert callback.bot is not None  # noqa: S101 — callbacks always carry a bot
        extra = {"specialist_id": specialist_id, "client_id": client_id}
        try:
            await callback.bot.send_message(client.telegram_chat_id, text)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("subscription.payment_reminder_send_failed", extra=extra)
            await record_client_message(
                self._session_factory,
                specialist_id=specialist_id,
                client_id=client_id,
                event=AuditEvent.PAYMENT_REMINDER,
                text=text,
                status=DeliveryStatus.FAILED,
                error=str(exc),
            )
            await callback.answer(self._m.not_delivered, show_alert=True)
            return
        logger.info("subscription.payment_reminder_sent", extra=extra)
        await record_client_message(
            self._session_factory,
            specialist_id=specialist_id,
            client_id=client_id,
            event=AuditEvent.PAYMENT_REMINDER,
            text=text,
            status=DeliveryStatus.SENT,
        )
        await callback.answer(self._m.sent, show_alert=True)


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="payment")
    router.callback_query.middleware(SpecialistMiddleware(session_factory))
    h = PaymentHandlers(messages, session_factory)
    router.callback_query.register(h.send_payment_reminder, F.data.startswith(_CB_SEND))
    return router
