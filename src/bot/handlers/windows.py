from datetime import UTC, datetime
import logging

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import SpecialistMiddleware
from src.bot.messages import BotMessages, WindowsMessages
from src.domain.schedule import format_ru_date, parse_working_days
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import DayWindows, list_free_windows
from src.services.specialists import get_settings

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 5


def render_windows(windows: list[DayWindows], m: WindowsMessages) -> str:
    """One message: each working day followed by its free `HH:MM` times.

    A day with no free windows still appears, with `empty_day`, so the count of
    five days stays predictable (see design.md, decision 2).
    """
    blocks = [m.title]
    for w in windows:
        times = ", ".join(w.free) if w.free else m.empty_day
        blocks.append(f"{m.day_header.format(date=format_ru_date(w.day))}\n{times}")
    return "\n\n".join(blocks)


class WindowsHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.windows
        self._session_factory = session_factory

    async def show(self, message: Message, specialist_id: int) -> None:
        async with self._session_factory() as session:
            specialist = await get_settings(
                SqlAlchemySpecialistsRepo(session), specialist_id
            )
            if specialist is None:  # pragma: no cover - middleware guarantees existence
                await message.answer(self._m.no_working_days)
                return
            if not parse_working_days(specialist.working_days):
                await message.answer(self._m.no_working_days)
                return
            windows = await list_free_windows(
                SqlAlchemyAppointmentsRepo(session),
                specialist=specialist,
                now=datetime.now(UTC),
                days=_WINDOW_DAYS,
            )
        await message.answer(render_windows(windows, self._m))


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="windows")
    router.message.middleware(SpecialistMiddleware(session_factory))

    h = WindowsHandlers(messages, session_factory)
    router.message.register(h.show, F.text == messages.windows.button)
    return router
