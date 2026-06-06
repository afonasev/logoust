from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import ClientsHandlers, SpecialistMiddleware
from src.bot.handlers.schedule import (
    RecurringHandlers,
    ScheduleHandlers,
)
from src.bot.messages import BotMessages
from src.domain.recurring import RecurringAppointment
from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.schedule import today_in_tz, wall_to_utc
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

_SP = 1
_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST
_CHAT = 555111


# --- fakes ------------------------------------------------------------------


class FakeState:
    def __init__(
        self, data: dict[str, Any] | None = None, state: object | None = None
    ) -> None:
        self.store: dict[str, Any] = dict(data or {})
        self.state = state

    async def get_data(self) -> dict[str, Any]:
        return dict(self.store)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.store.update(kwargs)
        return dict(self.store)

    async def set_state(self, state: object) -> None:
        self.state = state

    async def clear(self) -> None:
        self.store.clear()
        self.state = None


def _state(data: dict[str, Any] | None = None) -> Any:
    return FakeState(data)


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
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    return cb


def _texts(mock: AsyncMock) -> list[str]:
    return [c.args[0] for c in mock.await_args_list]


def _markup(mock: AsyncMock) -> Any:
    call = mock.await_args
    assert call is not None
    return call.kwargs["reply_markup"]


def _callbacks(markup: Any) -> list[str | None]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- seeding ----------------------------------------------------------------


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
        assert specialist.id is not None
        await SqlAlchemySpecialistsRepo(session).mark_welcomed(
            specialist.id,
            telegram_chat_id=_CHAT,
            telegram_username="spec",
            welcomed_at=datetime.now(UTC),
        )
    return specialist.id


async def _seed_client(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=_SP,
                child_name="Петя",
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
    assert client.id is not None
    return client.id


async def _seed_linked_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=_SP,
                child_name="Петя",
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
        assert client.id is not None
        await repo.link_telegram(
            client.id,
            telegram_chat_id=chat_id,
            username=None,
            linked_at=now,
            updated_at=now,
        )
    return client.id


async def _seed_series(  # noqa: PLR0913
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    weekday: int,
    start_date: date,
    time_hhmm: str = "14:00",
    active: bool = True,
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        series = await SqlAlchemyRecurringRepo(session).add(
            RecurringAppointment(
                id=None,
                specialist_id=_SP,
                client_id=client_id,
                weekday=weekday,
                time_hhmm=time_hhmm,
                comment="регулярная",
                active=active,
                start_date=start_date,
                materialized_through=start_date,
                created_at=now,
                updated_at=now,
            )
        )
    assert series.id is not None
    return series.id


def _recur(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> RecurringHandlers:
    return RecurringHandlers(messages, factory)


async def _load_series(
    factory: async_sessionmaker[AsyncSession], series_id: int
) -> RecurringAppointment | None:
    async with factory() as session:
        return await SqlAlchemyRecurringRepo(session).get_for_specialist(series_id, _SP)


# --- edit flow with custom time ---------------------------------------------


async def test_edit_flow_custom_time_and_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:1"), state, _SP)
    await h.ask_custom_time(_fake_callback("recur:other"), state)
    await h.apply_custom_time(_fake_message("10:30"), state, _SP)
    await h.apply_comment(_fake_message("принести тетрадь"), state, _SP)

    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.time_hhmm == "10:30"
    assert series.comment == "принести тетрадь"


async def test_edit_bad_custom_time_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:1"), state, _SP)
    await h.ask_custom_time(_fake_callback("recur:other"), state)
    msg = _fake_message("99:99")
    await h.apply_custom_time(msg, state, _SP)
    assert messages.recurring.bad_time in _texts(msg.answer)
    # The series rule is unchanged.
    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.weekday == 0


# --- series card + stop -----------------------------------------------------


async def test_show_card_renders_actions(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    # Opened from the client card → that origin is threaded into every action.
    back = f"clients:card:{client_id}"
    cb = _fake_callback(f"recur:card:{series_id}:2026-06-22~{back}")
    await h.show_card(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert f"recur:edit:{series_id}:2026-06-22~{back}" in callbacks
    assert f"recur:move:{series_id}:2026-06-22~{back}" in callbacks
    assert f"recur:skipask:{series_id}:2026-06-22~{back}" in callbacks
    assert f"recur:stopask:{series_id}~{back}" in callbacks
    # The back button returns to the client card, not the day view.
    assert back in callbacks


async def test_show_card_foreign_series_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:card:{series_id}:2026-06-22")
    await h.show_card(cb, 999)  # another specialist
    cb.answer.assert_awaited()
    cb.message.edit_text.assert_not_awaited()


async def test_show_card_shows_moved_occurrence_time(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    # Move the 06-22 occurrence to Wednesday 06-24 16:00 local (11:00 UTC).
    async with session_factory() as session:
        await SqlAlchemyRecurringExceptionsRepo(session).upsert(
            series_id,
            date(2026, 6, 22),
            new_starts_at=datetime(2026, 6, 24, 11, 0, tzinfo=UTC),
            created_at=datetime.now(UTC),
        )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:card:{series_id}:2026-06-22")
    await h.show_card(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    # The card shows the base rule explicitly...
    assert "Каждый понедельник в 14:00" in text
    # ...and the moved next-meeting date/time, not the series' default 14:00.
    assert "24 июня" in text
    assert "16:00" in text


async def test_show_card_marks_confirmed_occurrence(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    # Confirm the 2026-06-22 occurrence (14:00 wall in +05 → 09:00 UTC).
    starts_at = wall_to_utc(date(2026, 6, 22), "14:00", "Asia/Yekaterinburg")
    async with session_factory() as session:
        repo = SqlAlchemyRemindersRepo(session)
        reminder = AppointmentReminder(
            id=None,
            specialist_id=_SP,
            client_id=client_id,
            starts_at=starts_at,
            series_id=series_id,
            origin_date=date(2026, 6, 22),
            status=ReminderStatus.PENDING,
            sent_at=datetime.now(UTC),
            responded_at=None,
        )
        await repo.insert_pending(reminder)
        assert reminder.id is not None
        await repo.set_status(reminder.id, ReminderStatus.CONFIRMED, datetime.now(UTC))
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:card:{series_id}:2026-06-22")
    await h.show_card(cb, _SP)
    assert messages.reminder.card_confirmed in _texts(cb.message.edit_text)[0]


async def test_navigate_move_redraws_calendar(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _recur(messages, session_factory)
    cb = _fake_callback("recur:cal:2027:3")
    await h.navigate_move(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_reply_markup))
    # The redrawn calendar uses the recurring namespace for day/nav callbacks.
    assert any(c and c.startswith("recur:day:2027:3:") for c in callbacks)


async def test_stop_flow_confirms_and_deactivates(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)

    confirm = _fake_callback(f"recur:stopask:{series_id}")
    await h.confirm_stop(confirm, _SP)
    assert f"recur:stop:{series_id}" in _callbacks(_markup(confirm.message.edit_text))

    do = _fake_callback(f"recur:stop:{series_id}")
    await h.do_stop(do, _SP)
    assert messages.recurring.stopped in _texts(do.message.edit_text)
    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.active is False


async def test_stop_foreign_series_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    await h.confirm_stop(_fake_callback(f"recur:stopask:{series_id}"), 999)
    do = _fake_callback(f"recur:stop:{series_id}")
    await h.do_stop(do, 999)
    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.active is True


# --- edit flow --------------------------------------------------------------


async def test_edit_flow_changes_rule(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:3"), state, _SP)
    await h.pick_slot(_fake_callback("recur:slot:1100"), state, _SP)
    await h.apply_comment(_fake_message("новый"), state, _SP)

    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.weekday == 3
    assert series.time_hhmm == "11:00"
    assert series.comment == "новый"


async def test_edit_flow_skip_comment_clears_it(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:0"), state, _SP)
    await h.pick_slot(_fake_callback("recur:slot:1100"), state, _SP)
    await h.skip_comment(_fake_callback("recur:skipc"), state, _SP)

    series = await _load_series(session_factory, series_id)
    assert series is not None
    assert series.comment is None
    assert series.time_hhmm == "11:00"


async def test_edit_foreign_series_reports_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    # A second, valid specialist who does not own the series.
    async with session_factory() as session:
        other = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert other.id is not None
    assert other.id != _SP
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:3"), state, other.id)
    await h.pick_slot(_fake_callback("recur:slot:1100"), state, other.id)
    msg = _fake_message("x")
    await h.apply_comment(msg, state, other.id)
    assert messages.recurring.not_found in _texts(msg.answer)


# --- skip flow --------------------------------------------------------------


async def test_skip_flow_creates_exception(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    await h.confirm_skip(_fake_callback(f"recur:skipask:{series_id}:2026-06-22"), _SP)
    do = _fake_callback(f"recur:skip:{series_id}:2026-06-22")
    await h.do_skip(do, _SP)
    assert messages.recurring.skipped in _texts(do.message.edit_text)
    async with session_factory() as session:
        exc = await SqlAlchemyRecurringExceptionsRepo(session).list_for_series(
            series_id
        )
    assert len(exc) == 1
    assert exc[0].new_starts_at is None


async def test_skip_foreign_series_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    await h.confirm_skip(_fake_callback(f"recur:skipask:{series_id}:2026-06-22"), 999)
    await h.do_skip(_fake_callback(f"recur:skip:{series_id}:2026-06-22"), 999)
    async with session_factory() as session:
        exc = await SqlAlchemyRecurringExceptionsRepo(session).list_for_series(
            series_id
        )
    assert exc == []


# --- move flow --------------------------------------------------------------


async def test_move_flow_creates_moved_exception(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    # Move flow: pick a new date, then a new time.
    await h.start_move(_fake_callback(f"recur:move:{series_id}:2026-06-22"), state, _SP)
    await h.pick_move_day(_fake_callback("recur:day:2026:6:24"), state, _SP)
    do = _fake_callback("recur:slot:1600")
    await h.pick_slot(do, state, _SP)
    assert messages.recurring.moved in _texts(do.message.answer)
    async with session_factory() as session:
        exc = await SqlAlchemyRecurringExceptionsRepo(session).list_for_series(
            series_id
        )
    assert len(exc) == 1
    # The original Monday (06-22) is moved to Wednesday 06-24 16:00 local → 11:00 UTC.
    assert exc[0].original_date == date(2026, 6, 22)
    assert exc[0].new_starts_at == datetime(2026, 6, 24, 11, 0, tzinfo=UTC)


async def test_move_foreign_series_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    cb = _fake_callback(f"recur:move:{series_id}:2026-06-22")
    await h.start_move(cb, state, 999)
    cb.message.edit_text.assert_not_awaited()


async def test_move_reports_not_found_for_foreign_series(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    async with session_factory() as session:
        other = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert other.id is not None
    h = _recur(messages, session_factory)
    # State as if start_move + pick_move_day ran, but finished by a different owner.
    state = _state(
        {
            "flow": "move",
            "series_id": series_id,
            "origin_date": "2026-06-22",
            "new_day": "2026-06-24",
        }
    )
    msg_cb = _fake_callback("recur:slot:1600")
    await h.pick_slot(msg_cb, state, other.id)
    assert messages.recurring.not_found in _texts(msg_cb.message.answer)


async def test_cancel_clears_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _recur(messages, session_factory)
    state = _state({"flow": "create"})
    cb = _fake_callback("recur:cancel")
    await h.cancel(cb, state)
    assert messages.recurring.cancelled in _texts(cb.message.edit_text)
    assert await state.get_data() == {}


# --- notify the client (series + per-date) ----------------------------------


async def test_edit_asks_series_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_edit(_fake_callback(f"recur:edit:{series_id}:2026-06-22"), state)
    await h.pick_weekday(_fake_callback("recur:wd:3"), state, _SP)  # Thursday
    await h.pick_slot(_fake_callback("recur:slot:1100"), state, _SP)
    msg = _fake_message("новый")
    await h.apply_comment(msg, state, _SP)
    # Series edit → "modified" prompt describing the new weekly rule.
    cbs = _callbacks(_markup(msg.answer))
    assert any(c and c.startswith(f"sched:ntfs:m:{client_id}:") for c in cbs)
    assert "sched:ntfno" in cbs


async def test_stop_asks_series_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    do = _fake_callback(f"recur:stop:{series_id}")
    await h.do_stop(do, _SP)
    assert messages.recurring.stopped in _texts(do.message.edit_text)
    cbs = _callbacks(_markup(do.message.answer))
    assert any(c and c.startswith(f"sched:ntfs:x:{client_id}:") for c in cbs)


async def test_move_asks_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_move(_fake_callback(f"recur:move:{series_id}:2026-06-22"), state, _SP)
    await h.pick_move_day(_fake_callback("recur:day:2026:6:24"), state, _SP)
    do = _fake_callback("recur:slot:1600")
    await h.pick_slot(do, state, _SP)
    # Moving one date → single-occurrence "rescheduled" prompt (concrete date).
    cbs = _callbacks(_markup(do.message.answer))
    assert any(c and c.startswith(f"sched:ntf:r:{client_id}:") for c in cbs)
    assert "sched:ntfno" in cbs


async def test_skip_asks_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    series_id = await _seed_series(
        session_factory, client_id=client_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    do = _fake_callback(f"recur:skip:{series_id}:2026-06-22")
    await h.do_skip(do, _SP)
    assert messages.recurring.skipped in _texts(do.message.edit_text)
    # Cancelling one date → single-occurrence "cancelled" prompt (concrete date).
    cbs = _callbacks(_markup(do.message.answer))
    assert any(c and c.startswith(f"sched:ntf:x:{client_id}:") for c in cbs)


# --- rendering of virtual occurrences ---------------------------------------


async def test_day_view_shows_series_button(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    view_day = today + timedelta(days=14)
    await _seed_series(
        session_factory,
        client_id=client_id,
        weekday=today.weekday(),
        start_date=today,
    )
    h = ScheduleHandlers(messages, session_factory)
    cb = _fake_callback(f"sched:day_view:{view_day.isoformat()}")
    await h.open_day(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert any(c and c.startswith("recur:card:") for c in callbacks)


async def test_week_marks_series_line(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    await _seed_series(
        session_factory,
        client_id=client_id,
        weekday=today.weekday(),
        start_date=today,
    )
    h = ScheduleHandlers(messages, session_factory)
    cb = _fake_callback("sched:week")
    await h.show_week(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    assert messages.recurring.mark in text


async def test_client_card_links_series_occurrence(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    await _seed_series(
        session_factory,
        client_id=client_id,
        weekday=today.weekday(),
        start_date=today,
    )
    h = ClientsHandlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client_id}")
    await h.show_card(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    # The series occurrence links to the series card; there is no separate
    # "create regular" button anymore (it lives in the normal записать flow).
    assert any(c and c.startswith("recur:card:") for c in callbacks)
    assert not any(c and c.startswith("recur:new:") for c in callbacks)


# --- middleware settle (7.2) ------------------------------------------------


async def test_middleware_settles_once_per_day(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    # A series that started two weeks ago has passed occurrences to freeze.
    start = today - timedelta(days=14)
    series_id = await _seed_series(
        session_factory,
        client_id=client_id,
        weekday=start.weekday(),
        start_date=start,
    )
    mw = SpecialistMiddleware(session_factory)

    calls = {"n": 0}

    async def handler(_event: Any, _data: dict[str, Any]) -> str:  # noqa: RUF029
        calls["n"] += 1
        return "ok"

    event = AsyncMock()
    data = {"event_from_user": AsyncMock(id=_CHAT)}
    await mw(handler, event, data)

    async with session_factory() as session:
        from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo

        rows = await SqlAlchemyAppointmentsRepo(session).list_for_specialist_between(
            _SP,
            start=datetime(2000, 1, 1, tzinfo=UTC),
            end=datetime(2100, 1, 1, tzinfo=UTC),
        )
    materialized = len(rows)
    assert materialized >= 1  # at least one past occurrence frozen
    assert all(r.series_id == series_id for r in rows)
    assert data["specialist_id"] == _SP
    assert calls["n"] == 1

    # Second interaction the same day must not create duplicates.
    await mw(handler, event, {"event_from_user": AsyncMock(id=_CHAT)})
    async with session_factory() as session:
        from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo

        rows2 = await SqlAlchemyAppointmentsRepo(session).list_for_specialist_between(
            _SP,
            start=datetime(2000, 1, 1, tzinfo=UTC),
            end=datetime(2100, 1, 1, tzinfo=UTC),
        )
    assert len(rows2) == materialized
