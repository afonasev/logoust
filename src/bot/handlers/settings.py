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
from src.domain.schedule import (
    RU_WEEKDAYS_SHORT,
    RUSSIAN_TIMEZONES,
    parse_working_days,
)
from src.domain.specialist import Specialist
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.specialists import (
    SettingField,
    SettingsUpdateResult,
    get_settings,
    toggle_reminder,
    toggle_working_day,
    update_setting,
)

logger = logging.getLogger(__name__)

_TZ_LABELS = dict(RUSSIAN_TIMEZONES)

_CB_MENU = "settings:menu"
_CB_TZLIST = "settings:tzlist"
_CB_DAY_START = "settings:day_start"
_CB_DAY_END = "settings:day_end"
_CB_SLOT = "settings:slot"
_CB_WORKDAYS = "settings:workdays"
_CB_TOGGLE_DAY = "settings:wd:"  # + weekday index 0-6
_CB_REMINDER_TOGGLE = "settings:reminder"
_CB_REMINDER_TIME = "settings:reminder_time"

# Maps the FSM step to the setting it edits and the prompt/error texts.
_FIELD_BY_CALLBACK = {
    _CB_DAY_START: SettingField.DAY_START,
    _CB_DAY_END: SettingField.DAY_END,
    _CB_SLOT: SettingField.SLOT_MINUTES,
    _CB_REMINDER_TIME: SettingField.REMINDER_TIME,
}


class EditSetting(StatesGroup):
    day_start = State()
    day_end = State()
    slot = State()
    reminder_time = State()


_STATE_BY_FIELD = {
    SettingField.DAY_START: EditSetting.day_start,
    SettingField.DAY_END: EditSetting.day_end,
    SettingField.SLOT_MINUTES: EditSetting.slot,
    SettingField.REMINDER_TIME: EditSetting.reminder_time,
}


def _menu_keyboard(specialist: Specialist, m: SettingsMessages) -> InlineKeyboardMarkup:
    state = m.state_on if specialist.reminder_enabled else m.state_off
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m.btn_timezone, callback_data=_CB_TZLIST)],
            [
                InlineKeyboardButton(text=m.btn_day_start, callback_data=_CB_DAY_START),
                InlineKeyboardButton(text=m.btn_day_end, callback_data=_CB_DAY_END),
            ],
            [InlineKeyboardButton(text=m.btn_slot, callback_data=_CB_SLOT)],
            [InlineKeyboardButton(text=m.btn_working_days, callback_data=_CB_WORKDAYS)],
            [
                InlineKeyboardButton(
                    text=m.btn_reminder.format(state=state),
                    callback_data=_CB_REMINDER_TOGGLE,
                ),
                InlineKeyboardButton(
                    text=m.btn_reminder_time, callback_data=_CB_REMINDER_TIME
                ),
            ],
        ]
    )


def _timezone_keyboard(m: SettingsMessages) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"settings:settz:{value}")]
        for value, label in RUSSIAN_TIMEZONES
    ]
    rows.append([InlineKeyboardButton(text=m.btn_timezone, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _working_days_keyboard(
    working_days: str, m: SettingsMessages
) -> InlineKeyboardMarkup:
    working = set(parse_working_days(working_days))
    toggles = [
        InlineKeyboardButton(
            text=f"{'✅' if i in working else '⬜'} {RU_WEEKDAYS_SHORT[i]}",
            callback_data=f"{_CB_TOGGLE_DAY}{i}",
        )
        for i in range(len(RU_WEEKDAYS_SHORT))
    ]
    # Four toggles on the first row, three on the second, then Back.
    rows = [toggles[:4], toggles[4:]]
    rows.append([InlineKeyboardButton(text=m.btn_back, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_working_days(working_days: str, m: SettingsMessages) -> str:
    days = parse_working_days(working_days)
    if not days:
        return m.no_working_days
    return ", ".join(RU_WEEKDAYS_SHORT[d] for d in days)


def render_settings(specialist: Specialist, m: SettingsMessages) -> str:
    return m.title.format(
        timezone=_TZ_LABELS.get(specialist.timezone, specialist.timezone),
        day_start=specialist.day_start,
        day_end=specialist.day_end,
        slot=specialist.slot_minutes,
        working_days=_format_working_days(specialist.working_days, m),
        reminders=m.state_on if specialist.reminder_enabled else m.state_off,
        reminder_time=specialist.reminder_time,
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
            render_settings(specialist, self._m),
            reply_markup=_menu_keyboard(specialist, self._m),
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
            render_settings(specialist, self._m),
            reply_markup=_menu_keyboard(specialist, self._m),
        )
        await callback.answer()

    async def toggle_reminder(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        async with self._session_factory() as session:
            await toggle_reminder(
                SqlAlchemySpecialistsRepo(session), specialist_id=specialist_id
            )
        await self.open_menu(callback, state, specialist_id)

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

    async def show_working_days(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        specialist = await self._load(specialist_id)
        if specialist is None:  # pragma: no cover - middleware guarantees existence
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.pick_working_days,
            reply_markup=_working_days_keyboard(specialist.working_days, self._m),
        )
        await callback.answer()

    async def toggle_day(self, callback: CallbackQuery, specialist_id: int) -> None:
        weekday = int((callback.data or "").removeprefix(_CB_TOGGLE_DAY))
        async with self._session_factory() as session:
            updated = await toggle_working_day(
                SqlAlchemySpecialistsRepo(session),
                specialist_id=specialist_id,
                weekday=weekday,
            )
        if updated is None:  # pragma: no cover - middleware guarantees existence
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_reply_markup(
            reply_markup=_working_days_keyboard(updated.working_days, self._m)
        )
        await callback.answer()

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
        if field is SettingField.REMINDER_TIME:
            return self._m.ask_reminder_time
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
            render_settings(specialist, self._m),
            reply_markup=_menu_keyboard(specialist, self._m),
        )

    async def apply_day_start(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.DAY_START)

    async def apply_reminder_time(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(
            message, state, specialist_id, SettingField.REMINDER_TIME
        )

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
    router.message.register(h.apply_reminder_time, EditSetting.reminder_time)

    router.callback_query.register(h.open_menu, F.data == _CB_MENU)
    router.callback_query.register(h.show_timezones, F.data == _CB_TZLIST)
    router.callback_query.register(h.set_timezone, F.data.startswith("settings:settz:"))
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_START)
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_END)
    router.callback_query.register(h.ask_value, F.data == _CB_SLOT)
    router.callback_query.register(h.ask_value, F.data == _CB_REMINDER_TIME)
    router.callback_query.register(h.toggle_reminder, F.data == _CB_REMINDER_TOGGLE)
    router.callback_query.register(h.show_working_days, F.data == _CB_WORKDAYS)
    router.callback_query.register(h.toggle_day, F.data.startswith(_CB_TOGGLE_DAY))
    return router
