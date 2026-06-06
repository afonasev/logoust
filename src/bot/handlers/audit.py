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
from src.bot.messages import AuditMessages, BotMessages
from src.domain.audit import AuditEntry, AuditKind, DeliveryStatus
from src.domain.schedule import utc_to_wall
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.audit import AuditPage, list_audit
from src.services.clients import client_name_map
from src.services.specialists import get_settings

logger = logging.getLogger(__name__)

_PAGE_SIZE = 10
_CB_PREFIX = "audit:page:"


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _client_part(entry: AuditEntry, names: dict[int, str], m: AuditMessages) -> str:
    if entry.client_id is None:
        return ""
    child = names.get(entry.client_id)
    return m.client_suffix.format(child=child) if child is not None else ""


def _render_row(
    entry: AuditEntry, names: dict[int, str], tz: str, m: AuditMessages
) -> str:
    when = f"{utc_to_wall(entry.created_at, tz):%d.%m %H:%M}"
    event = m.events.get(entry.event.value, entry.event.value)
    client = _client_part(entry, names, m)
    if entry.kind is AuditKind.MESSAGE:
        icon = m.status_sent if entry.status is DeliveryStatus.SENT else m.status_failed
        return m.line_message.format(
            icon=icon, when=when, event=event, client=client, text=entry.text or ""
        )
    return m.line_action.format(
        icon=m.action_icon, when=when, event=event, client=client
    )


def render_feed(
    page: AuditPage, names: dict[int, str], tz: str, m: AuditMessages
) -> str:
    if not page.entries:
        return m.empty
    rows = [_render_row(e, names, tz, m) for e in page.entries]
    return "\n\n".join([m.title, *rows])


def _keyboard(page: AuditPage, m: AuditMessages) -> InlineKeyboardMarkup | None:
    # "позже" = newer (lower page index), "раньше" = older — the feed is newest-first.
    nav: list[InlineKeyboardButton] = []
    if page.has_prev:
        nav.append(
            InlineKeyboardButton(
                text=m.btn_prev, callback_data=f"{_CB_PREFIX}{page.page - 1}"
            )
        )
    if page.has_next:
        nav.append(
            InlineKeyboardButton(
                text=m.btn_next, callback_data=f"{_CB_PREFIX}{page.page + 1}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[nav]) if nav else None


class AuditHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.audit
        self._session_factory = session_factory

    async def _view(
        self, specialist_id: int, page_num: int
    ) -> tuple[str, InlineKeyboardMarkup | None]:
        async with self._session_factory() as session:
            page = await list_audit(
                SqlAlchemyAuditRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_PAGE_SIZE,
            )
            specialist = await get_settings(
                SqlAlchemySpecialistsRepo(session), specialist_id
            )
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        text = render_feed(page, names, specialist.timezone, self._m)
        return text, _keyboard(page, self._m)

    async def show(self, message: Message, specialist_id: int) -> None:
        text, keyboard = await self._view(specialist_id, 0)
        await message.answer(text, reply_markup=keyboard)

    async def page(self, callback: CallbackQuery, specialist_id: int) -> None:
        page_num = int((callback.data or "").rsplit(":", 1)[1])
        text, keyboard = await self._view(specialist_id, page_num)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="audit")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = AuditHandlers(messages, session_factory)
    router.message.register(h.show, F.text == messages.audit.button)
    router.callback_query.register(h.page, F.data.startswith(_CB_PREFIX))
    return router
