from datetime import UTC, datetime
import logging
from typing import cast

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.client_templates import template_default
from src.bot.handlers.clients import SpecialistMiddleware
from src.bot.messages import BotMessages, SettingsMessages, TemplatesMessages
from src.domain.message_template import (
    CLIENT_TEMPLATES,
    TemplateSpec,
    TemplateViolation,
    Violation,
)
from src.domain.schedule import (
    RU_WEEKDAYS_SHORT,
    RUSSIAN_TIMEZONES,
    parse_working_days,
)
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.digest import collect_today_digest
from src.services.message_templates import (
    reset_template,
    resolve_template,
    save_template_override,
)
from src.services.specialists import (
    SettingField,
    SettingsUpdateResult,
    get_settings,
    toggle_digest,
    toggle_reminder,
    toggle_working_day,
    update_setting,
)

logger = logging.getLogger(__name__)

_TZ_LABELS = dict(RUSSIAN_TIMEZONES)

_CB_MENU = "settings:menu"
# Inert header button (names a feature group); answered silently so the tap does
# not leave a loading spinner.
_CB_NOOP = "settings:noop"
_CB_TZLIST = "settings:tzlist"
_CB_DAY_START = "settings:day_start"
_CB_DAY_END = "settings:day_end"
_CB_SLOT = "settings:slot"
_CB_WORKDAYS = "settings:workdays"
_CB_TOGGLE_DAY = "settings:wd:"  # + weekday index 0-6
_CB_REMINDER_TOGGLE = "settings:reminder"
_CB_REMINDER_TIME = "settings:reminder_time"
_CB_DIGEST_TOGGLE = "settings:digest"
_CB_DIGEST_TIME = "settings:digest_time"
_CB_DIGEST_NOW = "settings:digest_now"
_CB_SUBSCRIPTION = "settings:subscription_presets"
_CB_TEMPLATES = "settings:templates"
_CB_TPL_EDIT = "tpl:edit:"  # + template_key
_CB_TPL_RESET = "tpl:reset:"  # + template_key

# Maps the FSM step to the setting it edits and the prompt/error texts.
_FIELD_BY_CALLBACK = {
    _CB_DAY_START: SettingField.DAY_START,
    _CB_DAY_END: SettingField.DAY_END,
    _CB_SLOT: SettingField.SLOT_MINUTES,
    _CB_REMINDER_TIME: SettingField.REMINDER_TIME,
    _CB_DIGEST_TIME: SettingField.DIGEST_TIME,
    _CB_SUBSCRIPTION: SettingField.SUBSCRIPTION_PRESETS,
}


class EditSetting(StatesGroup):
    day_start = State()
    day_end = State()
    slot = State()
    reminder_time = State()
    digest_time = State()
    subscription_presets = State()


class EditTemplate(StatesGroup):
    body = State()


def _format_placeholders(names: frozenset[str]) -> str:
    return ", ".join(f"{{{name}}}" for name in sorted(names))


def _placeholder_hint(spec: TemplateSpec, m: TemplatesMessages) -> str:
    """Human list of a template's placeholders, required ones marked, for the prompt."""
    if not spec.allowed:
        return m.no_placeholders
    parts = []
    for name in sorted(spec.allowed):
        mark = m.required_mark if name in spec.required else ""
        parts.append(f"{{{name}}}{mark}")
    return ", ".join(parts)


def _render_violations(
    violations: list[Violation], key: str, m: TemplatesMessages
) -> str:
    """Turn structured validation violations into one user-facing error message."""
    spec = CLIENT_TEMPLATES[key]
    allowed = _format_placeholders(spec.allowed) or m.no_placeholders
    lines: list[str] = []
    for v in violations:
        if v.kind is TemplateViolation.EMPTY:
            lines.append(m.err_empty)
        elif v.kind is TemplateViolation.MALFORMED:
            lines.append(m.err_malformed)
        elif v.kind is TemplateViolation.DISALLOWED:
            bad = ", ".join(f"{{{p}}}" for p in v.placeholders)
            lines.append(m.err_disallowed.format(bad=bad, allowed=allowed))
        else:  # MISSING_REQUIRED
            missing = ", ".join(f"{{{p}}}" for p in v.placeholders)
            lines.append(m.err_missing.format(missing=missing))
    return "\n".join(lines)


def _templates_keyboard(m: TemplatesMessages, back_text: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=m.labels[key], callback_data=f"{_CB_TPL_EDIT}{key}"
            ),
            InlineKeyboardButton(
                text=m.btn_reset, callback_data=f"{_CB_TPL_RESET}{key}"
            ),
        ]
        for key in CLIENT_TEMPLATES
    ]
    rows.append([InlineKeyboardButton(text=back_text, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_STATE_BY_FIELD = {
    SettingField.DAY_START: EditSetting.day_start,
    SettingField.DAY_END: EditSetting.day_end,
    SettingField.SLOT_MINUTES: EditSetting.slot,
    SettingField.REMINDER_TIME: EditSetting.reminder_time,
    SettingField.DIGEST_TIME: EditSetting.digest_time,
    SettingField.SUBSCRIPTION_PRESETS: EditSetting.subscription_presets,
}


def _menu_keyboard(
    specialist: Specialist, m: SettingsMessages, templates_btn: str
) -> InlineKeyboardMarkup:
    reminder_toggle = (
        m.btn_reminder_on if specialist.reminder_enabled else m.btn_reminder_off
    )
    digest_toggle = (
        m.btn_digest_on if specialist.morning_notify_enabled else m.btn_digest_off
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m.btn_timezone, callback_data=_CB_TZLIST)],
            [
                InlineKeyboardButton(text=m.btn_day_start, callback_data=_CB_DAY_START),
                InlineKeyboardButton(text=m.btn_day_end, callback_data=_CB_DAY_END),
            ],
            [
                InlineKeyboardButton(text=m.btn_slot, callback_data=_CB_SLOT),
                InlineKeyboardButton(
                    text=m.btn_working_days, callback_data=_CB_WORKDAYS
                ),
            ],
            # Non-clickable header so the row below reads as one feature group.
            [InlineKeyboardButton(text=m.btn_reminder, callback_data=_CB_NOOP)],
            [
                InlineKeyboardButton(
                    text=reminder_toggle, callback_data=_CB_REMINDER_TOGGLE
                ),
                InlineKeyboardButton(
                    text=m.btn_reminder_time, callback_data=_CB_REMINDER_TIME
                ),
            ],
            [InlineKeyboardButton(text=m.btn_digest, callback_data=_CB_NOOP)],
            [
                InlineKeyboardButton(
                    text=digest_toggle, callback_data=_CB_DIGEST_TOGGLE
                ),
                InlineKeyboardButton(
                    text=m.btn_digest_time, callback_data=_CB_DIGEST_TIME
                ),
                InlineKeyboardButton(
                    text=m.btn_digest_now, callback_data=_CB_DIGEST_NOW
                ),
            ],
            [
                InlineKeyboardButton(
                    text=m.btn_subscription_presets, callback_data=_CB_SUBSCRIPTION
                )
            ],
            [InlineKeyboardButton(text=templates_btn, callback_data=_CB_TEMPLATES)],
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
        digest=m.state_on if specialist.morning_notify_enabled else m.state_off,
        digest_time=specialist.morning_notify_time,
        subscription_presets=specialist.subscription_presets,
    )


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


class SettingsHandlers:  # noqa: PLR0904 — handler aggregator for the settings router
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.settings
        self._tm = messages.templates
        self._dm = messages.digest
        self._messages = messages
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
            reply_markup=_menu_keyboard(specialist, self._m, self._tm.btn_open),
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
            reply_markup=_menu_keyboard(specialist, self._m, self._tm.btn_open),
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

    async def toggle_digest(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        async with self._session_factory() as session:
            await toggle_digest(
                SqlAlchemySpecialistsRepo(session), specialist_id=specialist_id
            )
        await self.open_menu(callback, state, specialist_id)

    async def send_digest_now(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        # Manual check: send today's digest immediately, bypassing the schedule and
        # the enabled flag, and WITHOUT stamping the day — so the real morning
        # digest still fires (design.md, decision 6).
        specialist = await self._load(specialist_id)
        if specialist is None:  # pragma: no cover - middleware guarantees existence
            await callback.answer(self._m.not_found, show_alert=True)
            return
        assert specialist.telegram_chat_id is not None  # noqa: S101 — welcomed in settings
        async with self._session_factory() as session:
            text = await collect_today_digest(
                specialist,
                datetime.now(UTC),
                appointments_repo=SqlAlchemyAppointmentsRepo(session),
                recurring_repo=SqlAlchemyRecurringRepo(session),
                exceptions_repo=SqlAlchemyRecurringExceptionsRepo(session),
                clients_repo=SqlAlchemyClientsRepo(session),
                messages=self._dm,
            )
        if text is None:
            await callback.answer(self._m.digest_now_empty, show_alert=True)
            return
        assert callback.bot is not None  # noqa: S101 — callbacks always carry a bot
        try:
            await callback.bot.send_message(specialist.telegram_chat_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.warning(
                "specialist.digest_failed", extra={"specialist_id": specialist_id}
            )
            await callback.answer(self._m.digest_now_failed, show_alert=True)
            return
        await callback.answer()

    @staticmethod
    async def noop(callback: CallbackQuery) -> None:
        # Inert header button: just dismiss the loading spinner.
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
        if field is SettingField.DIGEST_TIME:
            return self._m.ask_digest_time
        if field is SettingField.SUBSCRIPTION_PRESETS:
            return self._m.ask_subscription_presets
        return self._m.ask_slot

    def _error(self, field: SettingField) -> str:
        if field is SettingField.SLOT_MINUTES:
            return self._m.bad_slot
        if field is SettingField.SUBSCRIPTION_PRESETS:
            return self._m.bad_subscription_presets
        return self._m.bad_time

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
            reply_markup=_menu_keyboard(specialist, self._m, self._tm.btn_open),
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

    async def apply_digest_time(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.DIGEST_TIME)

    async def apply_day_end(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.DAY_END)

    async def apply_slot(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(message, state, specialist_id, SettingField.SLOT_MINUTES)

    async def apply_subscription_presets(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await self.apply_value(
            message, state, specialist_id, SettingField.SUBSCRIPTION_PRESETS
        )

    # --- client message templates --------------------------------------------

    async def show_templates(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await _callback_message(callback).edit_text(
            self._tm.title,
            reply_markup=_templates_keyboard(self._tm, self._m.btn_back),
        )
        await callback.answer()

    async def edit_template(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        key = (callback.data or "").removeprefix(_CB_TPL_EDIT)
        async with self._session_factory() as session:
            current = await resolve_template(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist_id,
                key=key,
                default=template_default(self._messages, key),
            )
        await state.update_data(tpl_key=key)
        await state.set_state(EditTemplate.body)
        await _callback_message(callback).edit_text(
            self._tm.edit_prompt.format(
                label=self._tm.labels[key],
                current=current,
                placeholders=_placeholder_hint(CLIENT_TEMPLATES[key], self._tm),
            )
        )
        await callback.answer()

    async def apply_template(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        key = data.get("tpl_key")
        if key is None:  # pragma: no cover - state always carries the key
            await state.clear()
            return
        async with self._session_factory() as session:
            violations = await save_template_override(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist_id,
                key=key,
                body=message.text or "",
            )
        if violations:
            # Stay in the FSM state so the specialist can correct and resend.
            await message.answer(_render_violations(violations, key, self._tm))
            return
        await state.clear()
        await message.answer(self._tm.saved)
        await message.answer(
            self._tm.title,
            reply_markup=_templates_keyboard(self._tm, self._m.btn_back),
        )

    async def reset_template_action(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        key = (callback.data or "").removeprefix(_CB_TPL_RESET)
        async with self._session_factory() as session:
            removed = await reset_template(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist_id,
                key=key,
            )
        await callback.answer(
            self._tm.reset_done if removed else self._tm.reset_noop, show_alert=True
        )


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
    router.message.register(h.apply_digest_time, EditSetting.digest_time)
    router.message.register(
        h.apply_subscription_presets, EditSetting.subscription_presets
    )
    router.message.register(h.apply_template, EditTemplate.body)

    router.callback_query.register(h.open_menu, F.data == _CB_MENU)
    router.callback_query.register(h.noop, F.data == _CB_NOOP)
    router.callback_query.register(h.show_templates, F.data == _CB_TEMPLATES)
    router.callback_query.register(h.edit_template, F.data.startswith(_CB_TPL_EDIT))
    router.callback_query.register(
        h.reset_template_action, F.data.startswith(_CB_TPL_RESET)
    )
    router.callback_query.register(h.show_timezones, F.data == _CB_TZLIST)
    router.callback_query.register(h.set_timezone, F.data.startswith("settings:settz:"))
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_START)
    router.callback_query.register(h.ask_value, F.data == _CB_DAY_END)
    router.callback_query.register(h.ask_value, F.data == _CB_SLOT)
    router.callback_query.register(h.ask_value, F.data == _CB_REMINDER_TIME)
    router.callback_query.register(h.ask_value, F.data == _CB_DIGEST_TIME)
    router.callback_query.register(h.ask_value, F.data == _CB_SUBSCRIPTION)
    router.callback_query.register(h.toggle_reminder, F.data == _CB_REMINDER_TOGGLE)
    router.callback_query.register(h.toggle_digest, F.data == _CB_DIGEST_TOGGLE)
    router.callback_query.register(h.send_digest_now, F.data == _CB_DIGEST_NOW)
    router.callback_query.register(h.show_working_days, F.data == _CB_WORKDAYS)
    router.callback_query.register(h.toggle_day, F.data.startswith(_CB_TOGGLE_DAY))
    return router
