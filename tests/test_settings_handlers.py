from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.settings import (
    EditSetting,
    SettingsHandlers,
    render_settings,
)
from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.domain.specialist import Specialist
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import create_invite
from src.services.specialists import get_settings

_SP = 1


class FakeState:
    def __init__(self, state: object | None = None) -> None:
        self.store: dict[str, Any] = {}
        self.state = state

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.store.update(kwargs)
        return dict(self.store)

    async def set_state(self, state: object) -> None:
        self.state = state

    async def clear(self) -> None:
        self.store.clear()
        self.state = None


def _state(state: object | None = None) -> Any:
    return FakeState(state)


def _fake_message(text: str | None = None) -> AsyncMock:
    msg = AsyncMock()
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _fake_callback(data: str | None = None) -> AsyncMock:
    cb = AsyncMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def _texts(mock: AsyncMock) -> list[str]:
    return [c.args[0] for c in mock.await_args_list]


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert specialist.id is not None
    return specialist.id


def _handlers(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> SettingsHandlers:
    return SettingsHandlers(messages, factory)


def test_render_settings_shows_timezone_label():
    specialist = Specialist(
        id=1,
        invite_token="t",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
    )
    m = load_messages(DEFAULT_MESSAGES_PATH).settings
    text = render_settings(specialist, m)
    assert "Екатеринбург" in text  # default tz label
    assert "09:00" in text
    assert "Пн, Вт, Ср, Чт, Пт" in text  # noqa: RUF001 — default working days


def test_render_settings_shows_no_working_days_hint():
    specialist = Specialist(
        id=1,
        invite_token="t",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
        working_days="",
    )
    m = load_messages(DEFAULT_MESSAGES_PATH).settings
    assert m.no_working_days in render_settings(specialist, m)


async def test_show_menu_renders_settings(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    state = _state(state="x")
    await h.show_menu(msg, state, _SP)
    assert state.state is None
    assert "09:00" in _texts(msg.answer)[0]


async def test_open_menu_edits(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    await h.open_menu(cb, _state(), _SP)
    assert "Екатеринбург" in _texts(cb.message.edit_text)[0]


async def test_show_timezones_lists_options(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    await h.show_timezones(cb)
    assert _texts(cb.message.edit_text)[0] == messages.settings.pick_timezone


async def test_set_timezone_persists_and_returns_menu(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:settz:Europe/Moscow")
    await h.set_timezone(cb, _state(), _SP)
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.timezone == "Europe/Moscow"


async def test_ask_value_sets_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:day_start")
    state = _state()
    await h.ask_value(cb, state)
    assert state.state == EditSetting.day_start
    assert _texts(cb.message.edit_text)[0] == messages.settings.ask_day_start


async def test_ask_value_day_end_and_slot_prompts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)

    end_cb = _fake_callback("settings:day_end")
    end_state = _state()
    await h.ask_value(end_cb, end_state)
    assert end_state.state == EditSetting.day_end
    assert _texts(end_cb.message.edit_text)[0] == messages.settings.ask_day_end

    slot_cb = _fake_callback("settings:slot")
    slot_state = _state()
    await h.ask_value(slot_cb, slot_state)
    assert slot_state.state == EditSetting.slot
    assert _texts(slot_cb.message.edit_text)[0] == messages.settings.ask_slot


async def test_apply_day_start_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("8:15")
    state = _state(state=EditSetting.day_start)
    await h.apply_day_start(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.saved
    assert state.state is None
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.day_start == "08:15"


async def test_apply_day_end_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("99:99")
    state = _state(state=EditSetting.day_end)
    await h.apply_day_end(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.bad_time
    assert state.state == EditSetting.day_end


async def test_show_working_days_renders_toggle_screen(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:workdays")
    await h.show_working_days(cb, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.settings.pick_working_days


async def test_toggle_day_persists_and_redraws(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:wd:0")  # toggle Monday off (default is Mon-Fri)
    await h.toggle_day(cb, _SP)
    cb.message.edit_reply_markup.assert_awaited()
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.working_days == "1,2,3,4"


def test_render_settings_shows_reminder_state():
    specialist = Specialist(
        id=1,
        invite_token="t",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
    )
    m = load_messages(DEFAULT_MESSAGES_PATH).settings
    text = render_settings(specialist, m)
    assert m.state_on in text
    assert "12:00" in text  # default reminder time


async def test_toggle_reminder_flips_flag(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:reminder")
    await h.toggle_reminder(cb, _state(), _SP)
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.reminder_enabled is False


async def test_apply_reminder_time_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("9:30")
    state = _state(state=EditSetting.reminder_time)
    await h.apply_reminder_time(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.saved
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.reminder_time == "09:30"


async def test_apply_reminder_time_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("25:99")
    state = _state(state=EditSetting.reminder_time)
    await h.apply_reminder_time(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.bad_time
    assert state.state == EditSetting.reminder_time


async def test_ask_value_reminder_time_prompt(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:reminder_time")
    state = _state()
    await h.ask_value(cb, state)
    assert state.state == EditSetting.reminder_time
    assert _texts(cb.message.edit_text)[0] == messages.settings.ask_reminder_time


def test_render_settings_shows_subscription_default():
    specialist = Specialist(
        id=1,
        invite_token="t",
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
    )
    m = load_messages(DEFAULT_MESSAGES_PATH).settings
    assert "🎫 Встреч в абонементе: 8" in render_settings(specialist, m)


async def test_ask_value_subscription_default_prompt(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("settings:subscription_default")
    state = _state()
    await h.ask_value(cb, state)
    assert state.state == EditSetting.subscription_default
    assert _texts(cb.message.edit_text)[0] == messages.settings.ask_subscription_default


async def test_apply_subscription_default_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("10")
    state = _state(state=EditSetting.subscription_default)
    await h.apply_subscription_default(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.saved
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.subscription_default == 10


async def test_apply_subscription_default_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("abc")
    state = _state(state=EditSetting.subscription_default)
    await h.apply_subscription_default(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.settings.bad_subscription_default
    assert state.state == EditSetting.subscription_default


async def test_apply_slot_invalid_then_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)

    bad = _fake_message("zero")
    state = _state(state=EditSetting.slot)
    await h.apply_slot(bad, state, _SP)
    assert _texts(bad.answer)[0] == messages.settings.bad_slot

    good = _fake_message("45")
    await h.apply_slot(good, state, _SP)
    assert _texts(good.answer)[0] == messages.settings.saved
    async with session_factory() as session:
        updated = await get_settings(SqlAlchemySpecialistsRepo(session), _SP)
    assert updated is not None
    assert updated.slot_minutes == 45
