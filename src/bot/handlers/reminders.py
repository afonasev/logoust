"""Client-facing reminder responses: the `appt:cfm:` confirm/decline callback.

This router is deliberately NOT behind `SpecialistMiddleware` — the actor is the
*client*, not a specialist, so it must not be dropped as a non-onboarded user.
Owner isolation is enforced inside the service by matching the responder's chat to
the reminded client. On a decline, the specialist is notified with a button that
opens the appointment's card (one-off by id, virtual repeat by series/origin date).
"""

from datetime import UTC, datetime
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import BotMessages, ReminderMessages
from src.domain.reminder import AppointmentReminder
from src.domain.schedule import format_ru_date, utc_to_wall
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.reminder import apply_reminder_response

logger = logging.getLogger(__name__)

_CB_PREFIX = "appt:cfm:"


def build_confirm_callback(reminder_id: int, *, confirm: bool) -> str:
    """`appt:cfm:<reminder_id>:<y|n>` — fits the 64-byte callback_data limit."""
    return f"{_CB_PREFIX}{reminder_id}:{'y' if confirm else 'n'}"


def parse_confirm_callback(data: str | None) -> tuple[int, bool] | None:
    """Parse `appt:cfm:<id>:<y|n>` into `(reminder_id, confirm)`, or None."""
    if data is None or not data.startswith(_CB_PREFIX):
        return None
    raw_id, _, answer = data[len(_CB_PREFIX) :].partition(":")
    if not raw_id.isdigit() or answer not in {"y", "n"}:
        return None
    return int(raw_id), answer == "y"


def build_reminder_keyboard(
    reminder_id: int, m: ReminderMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_confirm,
                    callback_data=build_confirm_callback(reminder_id, confirm=True),
                ),
                InlineKeyboardButton(
                    text=m.btn_decline,
                    callback_data=build_confirm_callback(reminder_id, confirm=False),
                ),
            ]
        ]
    )


def _open_card_callback(
    reminder: AppointmentReminder, appointment_id: int | None
) -> str:
    # Virtual series repeat → series card by (series_id, origin_date); one-off →
    # its appointment card; a moved/missing one-off falls back to the day view.
    if reminder.series_id is not None and reminder.origin_date is not None:
        return f"recur:card:{reminder.series_id}:{reminder.origin_date.isoformat()}"
    if appointment_id is not None:
        return f"sched:card:{appointment_id}"
    return "sched:day_view:" + reminder.starts_at.date().isoformat()


class ReminderHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.reminder
        self._session_factory = session_factory

    async def confirm(self, callback: CallbackQuery) -> None:
        parsed = parse_confirm_callback(callback.data)
        if parsed is None or callback.from_user is None:  # pragma: no cover - guarded
            await callback.answer()
            return
        reminder_id, confirm = parsed
        async with self._session_factory() as session:
            response = await apply_reminder_response(
                SqlAlchemyRemindersRepo(session),
                SqlAlchemyClientsRepo(session),
                reminder_id=reminder_id,
                chat_id=callback.from_user.id,
                confirm=confirm,
                now=datetime.now(UTC),
            )
        if response is None:  # foreign / unknown reminder — silently dismiss
            await callback.answer()
            return
        toast = self._m.confirmed_toast if confirm else self._m.declined_toast
        await callback.answer(toast)
        if response.notify_specialist:
            await self._notify_specialist(callback, response.reminder)

    async def _notify_specialist(
        self, callback: CallbackQuery, reminder: AppointmentReminder
    ) -> None:
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(
                reminder.specialist_id
            )
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                reminder.client_id, reminder.specialist_id
            )
            appointment = await SqlAlchemyAppointmentsRepo(session).find_by_occurrence(
                reminder.specialist_id,
                reminder.client_id,
                starts_at=reminder.starts_at,
            )
        if specialist is None or specialist.telegram_chat_id is None:
            return  # pragma: no cover - reminders only exist for onboarded specialists
        tz = specialist.timezone
        wall = utc_to_wall(reminder.starts_at, tz)
        child = client.child_name if client is not None else ""
        text = self._m.specialist_declined.format(
            child=child, date=format_ru_date(wall.date()), time=f"{wall:%H:%M}"
        )
        assert callback.bot is not None  # noqa: S101 — callbacks always carry a bot
        appointment_id = appointment.id if appointment is not None else None
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self._m.btn_open_appt,
                        callback_data=_open_card_callback(reminder, appointment_id),
                    )
                ]
            ]
        )
        await callback.bot.send_message(
            specialist.telegram_chat_id, text, reply_markup=keyboard
        )


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="reminders")
    h = ReminderHandlers(messages, session_factory)
    router.callback_query.register(h.confirm, F.data.startswith(_CB_PREFIX))
    return router
