from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
from typing import Any, cast

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import BotMessages, ClientsMessages
from src.domain.client import Client, ClientStatus, ClientValidationError
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import (
    ArchivePage,
    EditResult,
    NewClient,
    add_client,
    archive_client,
    edit_client_field,
    list_archived_page,
    list_clients,
    restore_client,
)

logger = logging.getLogger(__name__)

# Archive list is paginated; active list is short enough to show in full.
_ARCHIVE_PAGE_SIZE = 8

# Field keys must match the editable fields accepted by services.edit_client_field.
_FIELD_LABELS = {
    "child_name": "Имя ребёнка",
    "contact_name": "Имя контакта",
    "contact_phone": "Телефон",
    "contact_telegram": "Telegram",
    "extra_contacts": "Доп. контакты",
    "note": "Заметка",
}

_BTN_ADD = "➕ Добавить"  # noqa: RUF001
_BTN_ACTIVE = "📋 Активные"
_BTN_ARCHIVED = "🗄 Архив"
_BTN_EDIT = "✏️ Изменить"
_BTN_ARCHIVE = "📦 В архив"  # noqa: RUF001
_BTN_RESTORE = "↩️ Вернуть"
_BTN_BACK = "⬅️ Назад"
_BTN_MENU = "⬅️ Меню"
_BTN_SKIP = "Пропустить"
_BTN_CANCEL = "Отмена"
_BTN_PREV = "◀"
_BTN_NEXT = "▶"

_CB_MENU = "clients:menu"
_CB_ADD = "clients:add"
_CB_SKIP = "clients:skip"
_CB_CANCEL = "clients:cancel"


class AddClient(StatesGroup):
    child_name = State()
    contact_name = State()
    contact_phone = State()
    contact_telegram = State()


class EditClient(StatesGroup):
    waiting_value = State()


def build_main_keyboard(messages: BotMessages) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура специалиста; рядом позже встанет «Расписание»."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=messages.clients.button)]],
        resize_keyboard=True,
    )


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_ADD, callback_data=_CB_ADD)],
            [
                InlineKeyboardButton(
                    text=_BTN_ACTIVE, callback_data="clients:list:active"
                ),
                InlineKeyboardButton(
                    text=_BTN_ARCHIVED, callback_data="clients:list:archived"
                ),
            ],
        ]
    )


def _list_keyboard(clients: list[Client]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=c.child_name, callback_data=f"clients:card:{c.id}")]
        for c in clients
    ]
    rows.append([InlineKeyboardButton(text=_BTN_MENU, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else ""


def _archive_keyboard(page: ArchivePage) -> InlineKeyboardMarkup:
    rows = []
    for c in page.clients:
        date = _fmt_date(c.archived_at)
        label = f"{c.child_name} · {date}" if date else c.child_name
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"clients:card:{c.id}")]
        )
    nav = []
    if page.has_prev:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_PREV, callback_data=f"clients:arch:{page.page - 1}"
            )
        )
    if page.has_next:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_NEXT, callback_data=f"clients:arch:{page.page + 1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=_BTN_MENU, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _card_keyboard(client: Client) -> InlineKeyboardMarkup:
    if client.status is ClientStatus.ARCHIVED:
        status_btn = InlineKeyboardButton(
            text=_BTN_RESTORE, callback_data=f"clients:restore:{client.id}"
        )
    else:
        status_btn = InlineKeyboardButton(
            text=_BTN_ARCHIVE, callback_data=f"clients:archive:{client.id}"
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_BTN_EDIT, callback_data=f"clients:edit:{client.id}"
                )
            ],
            [status_btn],
            [
                InlineKeyboardButton(
                    text=_BTN_BACK,
                    callback_data=f"clients:list:{client.status.value}",
                )
            ],
        ]
    )


def _edit_fields_keyboard(client_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=label, callback_data=f"clients:setfield:{client_id}:{field}"
            )
        ]
        for field, label in _FIELD_LABELS.items()
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=_BTN_BACK, callback_data=f"clients:card:{client_id}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_BTN_SKIP, callback_data=_CB_SKIP),
                InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL),
            ]
        ]
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)]
        ]
    )


def _or_dash(value: str | None, dash: str) -> str:
    return value or dash


def render_card(client: Client, m: ClientsMessages) -> str:
    if client.status is ClientStatus.ARCHIVED:
        status = m.status_archived
    else:
        status = m.status_active
    return m.card.format(
        child=client.child_name,
        contact=client.contact_name,
        phone=_or_dash(client.contact_phone, m.dash),
        telegram=_or_dash(client.contact_telegram, m.dash),
        extra=_or_dash(client.extra_contacts, m.dash),
        note=_or_dash(client.note, m.dash),
        status=status,
    )


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _parse_id(callback_data: str | None) -> int:
    # callback_data shape: "clients:<action>:<id>"; the id is the last segment.
    return int((callback_data or "").rsplit(":", 1)[1])


def _parse_page(callback_data: str | None) -> int:
    # "clients:arch:<n>" carries the page; the archive entry point implies page 0.
    if callback_data and callback_data.startswith("clients:arch:"):
        return int(callback_data.rsplit(":", 1)[1])
    return 0


class SpecialistMiddleware(BaseMiddleware):
    """Drop updates from non-onboarded users, inject specialist_id for the rest."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:  # pragma: no cover - messages/callbacks always carry a user
            return None
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).find_by_chat_id(
                user.id
            )
        if specialist is None:
            return None
        data["specialist_id"] = specialist.id
        return await handler(event, data)


class ClientsHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._messages = messages
        self._m = messages.clients
        self._session_factory = session_factory

    # --- menu -----------------------------------------------------------------

    async def show_menu(self, message: Message, state: FSMContext) -> None:
        # Pressing the reply button is also the escape hatch out of any wizard.
        await state.clear()
        await message.answer(self._m.menu_title, reply_markup=_menu_keyboard())

    async def open_menu(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await _callback_message(callback).edit_text(
            self._m.menu_title, reply_markup=_menu_keyboard()
        )
        await callback.answer()

    # --- listing & card -------------------------------------------------------

    async def show_list(self, callback: CallbackQuery, specialist_id: int) -> None:
        async with self._session_factory() as session:
            clients = await list_clients(
                SqlAlchemyClientsRepo(session),
                specialist_id=specialist_id,
                status=ClientStatus.ACTIVE,
            )
        text = self._m.list_active_title if clients else self._m.empty_active
        await _callback_message(callback).edit_text(
            text, reply_markup=_list_keyboard(clients)
        )
        await callback.answer()

    async def show_archive(self, callback: CallbackQuery, specialist_id: int) -> None:
        page_num = _parse_page(callback.data)
        async with self._session_factory() as session:
            page = await list_archived_page(
                SqlAlchemyClientsRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_ARCHIVE_PAGE_SIZE,
            )
        if page_num == 0 and not page.clients:
            text = self._m.empty_archived
        else:
            text = self._m.archive_title.format(page=page_num + 1)
        await _callback_message(callback).edit_text(
            text, reply_markup=_archive_keyboard(page)
        )
        await callback.answer()

    async def show_card(self, callback: CallbackQuery, specialist_id: int) -> None:
        await self._open_card(callback, specialist_id, _parse_id(callback.data))

    async def _open_card(
        self, callback: CallbackQuery, specialist_id: int, client_id: int
    ) -> None:
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        if client is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            render_card(client, self._m), reply_markup=_card_keyboard(client)
        )
        await callback.answer()

    # --- archive / restore ----------------------------------------------------

    async def archive(self, callback: CallbackQuery, specialist_id: int) -> None:
        client_id = _parse_id(callback.data)
        async with self._session_factory() as session:
            await archive_client(
                SqlAlchemyClientsRepo(session),
                client_id=client_id,
                specialist_id=specialist_id,
            )
        await self._open_card(callback, specialist_id, client_id)

    async def restore(self, callback: CallbackQuery, specialist_id: int) -> None:
        client_id = _parse_id(callback.data)
        async with self._session_factory() as session:
            await restore_client(
                SqlAlchemyClientsRepo(session),
                client_id=client_id,
                specialist_id=specialist_id,
            )
        await self._open_card(callback, specialist_id, client_id)

    # --- add wizard -----------------------------------------------------------

    async def start_add(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddClient.child_name)
        await _callback_message(callback).edit_text(
            self._m.ask_child_name, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def add_child_name(self, message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if not value:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.update_data(child_name=value)
        await state.set_state(AddClient.contact_name)
        await message.answer(self._m.ask_contact_name, reply_markup=_cancel_keyboard())

    async def add_contact_name(self, message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if not value:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.update_data(contact_name=value)
        await state.set_state(AddClient.contact_phone)
        await message.answer(self._m.ask_phone, reply_markup=_skip_keyboard())

    async def add_phone(self, message: Message, state: FSMContext) -> None:
        await state.update_data(contact_phone=(message.text or "").strip())
        await state.set_state(AddClient.contact_telegram)
        await message.answer(self._m.ask_telegram, reply_markup=_skip_keyboard())

    async def skip_phone(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(contact_phone=None)
        await state.set_state(AddClient.contact_telegram)
        await _callback_message(callback).edit_text(
            self._m.ask_telegram, reply_markup=_skip_keyboard()
        )
        await callback.answer()

    async def add_telegram(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await state.update_data(contact_telegram=(message.text or "").strip())
        await self._create(message, state, specialist_id)

    async def skip_telegram(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.update_data(contact_telegram=None)
        await self._create(_callback_message(callback), state, specialist_id)
        await callback.answer()

    async def _create(
        self, target: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        try:
            async with self._session_factory() as session:
                client = await add_client(
                    SqlAlchemyClientsRepo(session),
                    NewClient(
                        specialist_id=specialist_id,
                        child_name=data["child_name"],
                        contact_name=data["contact_name"],
                        contact_phone=data.get("contact_phone"),
                        contact_telegram=data.get("contact_telegram"),
                    ),
                )
        except ClientValidationError:
            # Only NO_CONTACT_CHANNEL is reachable here — required fields were
            # validated per step. Send the specialist back to the phone step.
            await state.set_state(AddClient.contact_phone)
            await target.answer(
                self._m.need_contact_channel, reply_markup=_skip_keyboard()
            )
            return
        await state.clear()
        await target.answer(self._m.added)
        await target.answer(
            render_card(client, self._m), reply_markup=_card_keyboard(client)
        )

    async def cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await _callback_message(callback).edit_text(
            self._m.cancelled, reply_markup=_menu_keyboard()
        )
        await callback.answer()

    # --- edit a field ---------------------------------------------------------

    @staticmethod
    async def start_edit(callback: CallbackQuery) -> None:
        client_id = _parse_id(callback.data)
        await _callback_message(callback).edit_reply_markup(
            reply_markup=_edit_fields_keyboard(client_id)
        )
        await callback.answer()

    async def pick_field(self, callback: CallbackQuery, state: FSMContext) -> None:
        _, _, raw_id, field = (callback.data or "").split(":", 3)
        await state.set_state(EditClient.waiting_value)
        await state.update_data(client_id=int(raw_id), field=field)
        prompt = self._m.edit_prompt.format(label=_FIELD_LABELS[field])
        await _callback_message(callback).edit_text(
            prompt, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def apply_edit(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        async with self._session_factory() as session:
            result = await edit_client_field(
                SqlAlchemyClientsRepo(session),
                client_id=data["client_id"],
                specialist_id=specialist_id,
                field=data["field"],
                value=message.text or "",
            )
        if result is EditResult.EMPTY_REQUIRED:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.clear()
        await message.answer(self._m.updated, reply_markup=_menu_keyboard())


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="clients")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = ClientsHandlers(messages, session_factory)

    router.message.register(h.show_menu, F.text == messages.clients.button)
    router.message.register(h.add_child_name, AddClient.child_name)
    router.message.register(h.add_contact_name, AddClient.contact_name)
    router.message.register(h.add_phone, AddClient.contact_phone)
    router.message.register(h.add_telegram, AddClient.contact_telegram)
    router.message.register(h.apply_edit, EditClient.waiting_value)

    router.callback_query.register(h.cancel, F.data == _CB_CANCEL)
    router.callback_query.register(h.open_menu, F.data == _CB_MENU)
    router.callback_query.register(h.start_add, F.data == _CB_ADD)
    router.callback_query.register(
        h.skip_phone, F.data == _CB_SKIP, AddClient.contact_phone
    )
    router.callback_query.register(
        h.skip_telegram, F.data == _CB_SKIP, AddClient.contact_telegram
    )
    router.callback_query.register(h.show_list, F.data == "clients:list:active")
    router.callback_query.register(h.show_archive, F.data == "clients:list:archived")
    router.callback_query.register(h.show_archive, F.data.startswith("clients:arch:"))
    router.callback_query.register(h.show_card, F.data.startswith("clients:card:"))
    router.callback_query.register(h.start_edit, F.data.startswith("clients:edit:"))
    router.callback_query.register(h.pick_field, F.data.startswith("clients:setfield:"))
    router.callback_query.register(h.archive, F.data.startswith("clients:archive:"))
    router.callback_query.register(h.restore, F.data.startswith("clients:restore:"))
    return router
