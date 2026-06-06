from datetime import UTC, datetime
import logging
from typing import cast

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import SpecialistMiddleware
from src.bot.messages import BotMessages, WindowsMessages
from src.domain.schedule import format_ru_date, parse_working_days
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import DayWindows, list_free_windows
from src.services.recurring import load_series_context
from src.services.specialists import get_settings

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 5
_CB_ALL = "windows:all"
_CB_ADJACENT = "windows:adjacent"
_ACTIVE_MARK = "● "


def _mode_keyboard(m: WindowsMessages, *, adjacent: bool) -> InlineKeyboardMarkup:
    """Two mode buttons; the active one is prefixed with a dot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(_ACTIVE_MARK if not adjacent else "") + m.button_all,
                    callback_data=_CB_ALL,
                ),
                InlineKeyboardButton(
                    text=(_ACTIVE_MARK if adjacent else "") + m.button_adjacent,
                    callback_data=_CB_ADJACENT,
                ),
            ]
        ]
    )


def render_windows(
    windows: list[DayWindows], m: WindowsMessages, *, adjacent: bool
) -> tuple[str, InlineKeyboardMarkup]:
    """One message: each working day followed by its free `HH:MM` times, plus the
    mode-switch keyboard with the active mode marked.

    A day with no free windows still appears, with `empty_day`, so the count of
    five days stays predictable (see design.md, decision 2). In `adjacent` mode an
    empty day is expected (no taken neighbours) and reuses the same `empty_day`.
    """
    blocks = [m.title]
    for w in windows:
        times = ", ".join(w.free) if w.free else m.empty_day
        blocks.append(f"{m.day_header.format(date=format_ru_date(w.day))}\n{times}")
    return "\n\n".join(blocks), _mode_keyboard(m, adjacent=adjacent)


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


class WindowsHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.windows
        self._session_factory = session_factory

    async def _view(
        self, specialist_id: int, *, adjacent: bool
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        """Build the windows message for a mode, or the hint when no working days.

        Returns (text, None) for the no-working-days hint (no mode switch makes
        sense without a grid) and (text, keyboard) otherwise.
        """
        async with self._session_factory() as session:
            specialist = await get_settings(
                SqlAlchemySpecialistsRepo(session), specialist_id
            )
            if specialist is None:  # pragma: no cover - middleware guarantees existence
                return self._m.no_working_days, None
            if not parse_working_days(specialist.working_days):
                return self._m.no_working_days, None
            # Future repeats of active series occupy slots too, so they must be
            # subtracted from free windows (availability spec).
            series = await load_series_context(
                SqlAlchemyRecurringScheduleRepo(session),
                SqlAlchemyRecurringSlotRepo(session),
                SqlAlchemyRecurringSlotOverrideRepo(session),
                specialist_id=specialist_id,
                now=datetime.now(UTC),
                tz=specialist.timezone,
            )
            windows = await list_free_windows(
                SqlAlchemyAppointmentsRepo(session),
                specialist=specialist,
                now=datetime.now(UTC),
                days=_WINDOW_DAYS,
                series=series,
                adjacent=adjacent,
            )
        return render_windows(windows, self._m, adjacent=adjacent)

    async def show(self, message: Message, specialist_id: int) -> None:
        text, keyboard = await self._view(specialist_id, adjacent=False)
        await message.answer(text, reply_markup=keyboard)

    async def switch(self, callback: CallbackQuery, specialist_id: int) -> None:
        adjacent = callback.data == _CB_ADJACENT
        text, keyboard = await self._view(specialist_id, adjacent=adjacent)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="windows")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = WindowsHandlers(messages, session_factory)
    router.message.register(h.show, F.text == messages.windows.button)
    router.callback_query.register(h.switch, F.data == _CB_ALL)
    router.callback_query.register(h.switch, F.data == _CB_ADJACENT)
    return router
