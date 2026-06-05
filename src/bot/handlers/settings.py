import logging
from typing import cast

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import SpecialistMiddleware
from src.bot.messages import BotMessages, SettingsMessages
from src.domain.schedule import RUSSIAN_TIMEZONES
from src.domain.specialist import Specialist
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.specialists import (
    SettingField,
    SettingsUpdateResult,
    get_settings,
    update_setting,
)

logger = logging.getLogger(__name__)

_TZ_LABELS = dict(RUSSIAN_TIMEZONES)

_CB_MENU = "settings:menu"
_CB_TZLIST = "settings:tzlist"
_CB_DAY_START = "settings:day_start"
_CB_DAY_END = "settings:day_end"
_CB_SLOT = "settings:slot"

# Maps the FSM step to the setting it edits and the prompt/error texts.
_FIELD_BY_CALLBACK = {
    _CB_DAY_START: SettingField.DAY_START,
    _CB_DAY_END: SettingField.DAY_END,
    _CB_SLOT: SettingField.SLOT_MINUTES,
}


class EditSetting(StatesGroup):
    day_start = State()
    day_end = State()
    slot = State()


_STATE_BY_FIELD = {
    SettingField.DAY_START: EditSetting.day_start,
    SettingField.DAY_END: EditSetting.day_end,
    SettingField.SLOT_MINUTES: EditSetting.slot,
}


def _menu_keyboard(m: SettingsMessages) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m.btn_timezone, callback_data=_CB_TZLIST)],
            [
                InlineKeyboardButton(text=m.btn_day_start, callback_data=_CB_DAY_START),
                InlineKeyboardButton(text=m.btn_day_end, callback_data=_CB_DAY_END),
            ],
            [InlineKeyboardButton(text=m.btn_slot, callback_data=_CB_SLOT)],
        ]
    )


def _timezone_keyboard(m: SettingsMessages) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"settings:settz:{value}")]
        for value, label in RUSSIAN_TIMEZONES
    ]
    rows.append([InlineKeyboardButton(text=m.btn_timezone, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_settings(specialist: Specialist, m: SettingsMessages) -> str:
    return m.title.format(
        timezone=_TZ_LABELS.get(specialist.timezone, specialist.timezone),
        day_start=specialist.day_start,
        day_end=specialist.day_end,
        slot=specialist.slot_minutes,
    )


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


class SettingsHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.settings
        self._session_factory = session_factory

    async def _load(self, specialist_id: int) -> Specialist | None:
        async with self._session_factory() as session:
            return await get_settings(SqlAlchemySpecialistsRepo(session), specialist_id)

    async def show_menu(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        # Pressing the reply button is also the escape hatch out of any wizard.
        await state.clear()
        specialist = await self._load(specialist_id)
        if specialist is None:  # pragma: no cover - middleware guarantees existence
            await message.answer(self._m.not_found)
            return
        await message.answer(
            render_settings(specialist, self._m), reply_markup=_menu_keyboard(self._m)
        )

    async def open_menu(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        specialist = await self._load(specialist_id)
        if specialist is None:  # pragma: no cover - middleware guarantees existence
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            render_settings(specialist, self._m), reply_markup=_menu_keyboard(self._m)
        )
        await callback.answer()

    async def show_timezones(self, callback: CallbackQuery) -> None:
        await _callback_message(callback).edit_text(
            self._m.pick_timezone, reply_markup=_timezone_keyboard(self._m)
        )
        await callback.answer()

    async def set_timezone(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        _, _, value = (callback.data or "").split(":", 2)
        async with self._session_factory() as session:
            await update_setting(
                SqlAlchemySpecialistsRepo(session),
                specialist_id=specialist_id,
                field=SettingField.TIMEZONE,
                raw=value,
            )
        await self.open_menu(callback, state, specialist_id)

    async def ask_value(self, callback: CallbackQuery, state: FSMContext) -> None:
        field = _FIELD_BY_CALLBACK[callback.data or ""]
        await state.set_state(_STATE_BY_FIELD[field])
        await _callback_message(callback).edit_text(self._prompt(field))
        await callback.answer()

    def _prompt(self, field: SettingField) -> str:
        if field is SettingField.DAY_START:
            return self._m.ask_day_start
        if field is SettingField.DAY_END:
            return self._m.ask_day_end
        return self._m.ask_slot

    def _error(self, field: SettingField) -> str:
        return (
            self._m.bad_slot if field is SettingField.SLOT_MINUTES else self._m.bad_time
        )

    async def apply_value(
        self,
        message: Message,
        state: FSMContext,
        specialist_id: int,
        field: SettingField,
    ) -> None:
        async with self._session_factory() as session:
            result = await update_setting(
                SqlAlchemySpecialistsRepo(session),
                specialist_id=specialist_id,
                field=field,
                raw=message.text or "",
            )
        if result is SettingsUpdateResult.INVALID:
            await message.answer(self._error(field))
            return
        await state.clear()
        specialist = await self._load(specialist_id)
        if specialist is None:  # pragma: no cover - middleware guarantees existence
            await message.answer(self._m.not_found)
            return
        await message.answer(self._m.saved)
        await message.answer(
            render_settings(specialist, self._m), reply_markup=_menu_keyboard(self._m)
        )

    async def apply_day_start(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.DAY_START)

    async def apply_day_end(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.DAY_END)

    async def apply_slot(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.SLOT_MINUTES)


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="settings")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = SettingsHandlers(messages, session_factory)

    router.message.register(h.show_menu, F.text == messages.settings.button)
    router.message.register(h.apply_day_start, EditSetting.day_start)
    router.message.register(h.apply_day_end, EditSetting.day_end)
    router.message.register(h.apply_slot, EditSetting.slot)

    router.callback_query.register(h.open_menu, F.data == _CB_MENU)
    router.callback_query.register(h.show_timezones, F.data == _CB_TZLIST)
    router.callback_query.register(h.set_timezone, F.data.startswith("settings:settz:"))
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_START)
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_END)
    router.callback_query.register(h.ask_value, F.data == _CB_SLOT)
    return router
