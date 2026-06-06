from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.schedule import (
    RecurringHandlers,
    Schedule,
    ScheduleHandlers,
    _build_navigator,
    build_calendar,
    build_slots_keyboard,
    render_card,
)
from src.bot.messages import BotMessages
from src.domain.appointment import Appointment
from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.schedule import format_ru_date, today_in_tz, utc_to_wall, wall_to_utc
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.scheduled_messages_repo import (
    SqlAlchemyScheduledMessagesRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite
from src.services.recurring import add_slot, create_schedule

_SP = 1
_TZ = "Asia/Yekaterinburg"
_FUTURE = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
_PAST = datetime(2020, 1, 15, 9, 0, tzinfo=UTC)


# --- fakes --------------------------------------------------------------------


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


def _state(data: dict[str, Any] | None = None, state: object | None = None) -> Any:
    return FakeState(data, state)


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


def _button_texts(markup: Any) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _callbacks(markup: Any) -> list[str | None]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- seeding ------------------------------------------------------------------


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert specialist.id is not None
    return specialist.id


async def _seed_client(
    factory: async_sessionmaker[AsyncSession], child: str = "Петя"
) -> int:
    async with factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=_SP,
                child_name=child,
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
    assert client.id is not None
    return client.id


async def _seed_linked_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int, child: str = "Петя"
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=_SP,
                child_name=child,
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


async def _seed_appt(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    starts_at: datetime,
    comment: str | None = None,
) -> Appointment:
    now = datetime.now(UTC)
    async with factory() as session:
        return await SqlAlchemyAppointmentsRepo(session).add(
            Appointment(
                id=None,
                specialist_id=_SP,
                client_id=client_id,
                starts_at=starts_at,
                comment=comment,
                created_at=now,
                updated_at=now,
            )
        )


def _handlers(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> ScheduleHandlers:
    h = ScheduleHandlers(messages, factory)
    r = RecurringHandlers(messages, factory)
    h.set_navigator(_build_navigator(messages, factory, h, r))
    return h


# --- pure builders ------------------------------------------------------------


def test_build_calendar_disables_prev_in_current_month():
    today = date(2026, 6, 4)
    markup = build_calendar(2026, 6, today)
    header = markup.inline_keyboard[0]
    assert header[0].callback_data == "sched:noop"  # no prev in current month
    assert header[2].callback_data == "sched:cal:2026:7"


def test_build_calendar_enables_prev_in_future_month():
    today = date(2026, 6, 4)
    markup = build_calendar(2026, 7, today)
    assert markup.inline_keyboard[0][0].callback_data == "sched:cal:2026:6"


def test_build_calendar_past_day_inert_today_active():
    today = date(2026, 6, 4)
    markup = build_calendar(2026, 6, today)
    day_cbs = [c for c in _callbacks(markup) if c and c.startswith("sched:day:")]
    assert "sched:day:2026:6:4" in day_cbs  # today selectable
    assert "sched:day:2026:6:3" not in day_cbs  # yesterday inert (noop)


def test_build_calendar_highlights_today():
    today = date(2026, 6, 4)
    markup = build_calendar(2026, 6, today)
    # Today's cell carries the green marker; other days are plain numbers.
    today_btn = next(
        b
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data == "sched:day:2026:6:4"
    )
    assert today_btn.text == "🟢4"
    other_btn = next(
        b
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data == "sched:day:2026:6:5"
    )
    assert other_btn.text == "5"


def test_build_slots_keyboard_marks_taken_and_free():
    markup = build_slots_keyboard(["09:00", "10:00"], {"10:00"}, _messages_schedule())
    cbs = _callbacks(markup)
    assert "sched:slot:0900" in cbs
    assert "sched:other" in cbs
    assert "sched:cancel" in cbs
    labels = _button_texts(markup)
    assert "🟢 09:00" in labels  # free
    assert "🔴 10:00" in labels  # already booked at that time


def test_build_slots_keyboard_full_rows():
    # Exactly three slots fill a row, so no trailing partial row is appended.
    markup = build_slots_keyboard(
        ["09:00", "10:00", "11:00"], set(), _messages_schedule()
    )
    assert "sched:slot:1100" in _callbacks(markup)


def _messages_schedule():
    from src.bot.messages import DEFAULT_MESSAGES_PATH, load_messages

    return load_messages(DEFAULT_MESSAGES_PATH).schedule


def test_render_card_uses_dash_without_comment():
    appt = Appointment(
        id=1,
        specialist_id=_SP,
        client_id=2,
        starts_at=_FUTURE,
        comment=None,
        created_at=_FUTURE,
        updated_at=_FUTURE,
    )
    m = _messages_schedule()
    text = render_card(appt, "Петя", "Asia/Yekaterinburg", m)
    assert "Петя" in text
    assert m.dash in text


# --- create flow --------------------------------------------------------------


async def test_start_create_sets_state_and_shows_calendar(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:new:{client_id}")
    state = _state()
    await h.start_create(cb, state, _SP)
    assert state.store == {"flow": "create", "client_id": client_id}
    assert _texts(cb.message.edit_text)[0] == messages.schedule.pick_date


async def test_pick_day_shows_slots(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day:2030:1:15")
    state = _state(data={"flow": "create", "client_id": 1})
    await h.pick_day(cb, state, _SP)
    assert state.store["day"] == "2030-01-15"
    assert _texts(cb.message.edit_text)[0] == messages.schedule.pick_time
    # Nothing booked yet → every slot is marked free.
    labels = _button_texts(_markup(cb.message.edit_text))
    assert all("🔴" not in label for label in labels)


async def test_pick_day_marks_booked_slot(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    # 14:00 local (+05) on 2030-01-15 → 09:00 UTC.
    await _seed_appt(
        session_factory,
        client_id=client_id,
        starts_at=datetime(2030, 1, 15, 9, 0, tzinfo=UTC),
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day:2030:1:15")
    state = _state(data={"flow": "create", "client_id": client_id})
    await h.pick_day(cb, state, _SP)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert "🔴 14:00" in labels
    assert "🟢 09:00" in labels


async def test_pick_day_excludes_rescheduled_appointment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(
        session_factory,
        client_id=client_id,
        starts_at=datetime(2030, 1, 15, 9, 0, tzinfo=UTC),  # 14:00 local
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day:2030:1:15")
    # Reschedule flow: its own current slot must not show as taken.
    state = _state(data={"flow": "reschedule", "appointment_id": appt.id})
    await h.pick_day(cb, state, _SP)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert "🟢 14:00" in labels


async def test_pick_slot_create_asks_regular(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:slot:1400")
    state = _state(data={"flow": "create", "client_id": 1, "day": "2030-01-15"})
    await h.pick_slot(cb, state, _SP)
    # The create flow now asks "make it regular?" before the comment.
    assert state.store["hhmm"] == "14:00"
    assert _texts(cb.message.answer)[0] == messages.schedule.ask_regular


async def test_choose_regular_no_then_asks_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:reg:0")
    state = _state(
        data={"flow": "create", "client_id": 1, "day": "2030-01-15", "hhmm": "14:00"}
    )
    await h.choose_regular(cb, state)
    # "No" keeps the one-off flow and moves straight to the comment step.
    assert state.store["flow"] == "create"
    assert state.state == Schedule.comment
    assert _texts(cb.message.edit_text)[0] == messages.schedule.ask_comment


async def test_apply_comment_creates_appointment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("принести тетрадь")
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",
            "hhmm": "14:00",
        }
    )
    await h.apply_comment(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.schedule.created
    assert state.store == {}
    async with session_factory() as session:
        rows = await SqlAlchemyAppointmentsRepo(session).list_future_for_specialist(
            _SP, since=datetime(2029, 1, 1, tzinfo=UTC)
        )
    assert rows[0].comment == "принести тетрадь"


async def test_skip_comment_creates_without_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",
            "hhmm": "14:00",
        }
    )
    await h.skip_comment(cb, state, _SP)
    assert _texts(cb.message.answer)[0] == messages.schedule.created
    async with session_factory() as session:
        rows = await SqlAlchemyAppointmentsRepo(session).list_future_for_specialist(
            _SP, since=datetime(2029, 1, 1, tzinfo=UTC)
        )
    assert rows[0].comment is None


async def test_choose_regular_yes_seeds_first_slot_and_asks_add_more(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:reg:1")
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",  # a Tuesday
            "hhmm": "14:00",
        }
    )
    await h.choose_regular(cb, state)
    # "Yes" no longer creates the schedule here: it seeds the first slot into FSM
    # data and shows the "add another day?" step. Creation completes in
    # RecurringHandlers via recur:done (tested in test_recurring_handlers.py).
    assert state.store["flow"] == "rcreate"
    assert state.store["slots"] == [
        {
            "weekday": date(2030, 1, 15).weekday(),
            "hhmm": "14:00",
            "start_date": "2030-01-15",
        }
    ]
    assert _texts(cb.message.edit_text)[0] == messages.recurring.add_more
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert "recur:add" in cbs
    assert "recur:done" in cbs
    # Nothing is persisted at this step.
    async with session_factory() as session:
        schedules = await SqlAlchemyRecurringScheduleRepo(
            session
        ).list_active_for_specialist(_SP)
    assert schedules == []


async def test_custom_time_valid_then_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(data={"flow": "create", "client_id": 1, "day": "2030-01-15"})
    await h.ask_custom_time(cb, state)
    assert state.state == Schedule.custom_time

    msg = _fake_message("14:37")
    await h.apply_custom_time(msg, state, _SP)
    assert state.store["hhmm"] == "14:37"
    # After a valid time the create flow asks the "regular?" question.
    assert _texts(msg.answer)[0] == messages.schedule.ask_regular


async def test_custom_time_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("99:99")
    state = _state(data={"flow": "create", "client_id": 1, "day": "2030-01-15"})
    await h.apply_custom_time(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.schedule.bad_time


# --- reschedule flow ----------------------------------------------------------


async def test_reschedule_via_slot_moves_and_shows_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(
        session_factory, client_id=client_id, starts_at=_FUTURE, comment="не трогать"
    )
    h = _handlers(messages, session_factory)

    cb = _fake_callback(f"sched:resch:{appt.id}")
    state = _state()
    await h.start_reschedule(cb, state, _SP)
    assert state.store == {
        "flow": "reschedule",
        "appointment_id": appt.id,
        "back": "",
    }

    state.store["day"] = "2031-02-20"
    slot_cb = _fake_callback("sched:slot:1000")
    await h.pick_slot(slot_cb, state, _SP)
    assert _texts(slot_cb.message.answer)[0] == messages.schedule.rescheduled
    async with session_factory() as session:
        assert appt.id is not None
        moved = await SqlAlchemyAppointmentsRepo(session).get_for_specialist(
            appt.id, _SP
        )
    assert moved is not None
    assert moved.starts_at.year == 2031
    assert moved.comment == "не трогать"


async def test_reschedule_returns_to_origin_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    # Opened from the client card → the moved card's Back returns there, not the day.
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    origin = f"clients:card:{client_id}"
    state = _state()
    await h.start_reschedule(
        _fake_callback(f"sched:resch:{appt.id}~{origin}"), state, _SP
    )
    assert state.store["back"] == origin
    state.store["day"] = "2031-02-20"
    slot_cb = _fake_callback("sched:slot:1000")
    await h.pick_slot(slot_cb, state, _SP)
    # The freshest screen is the moved appointment card; its Back is the origin.
    keyboard = _markup(slot_cb.message.answer)
    assert keyboard.inline_keyboard[-1][0].callback_data == origin


async def test_reschedule_missing_reports_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:slot:1000")
    state = _state(
        data={"flow": "reschedule", "appointment_id": 999, "day": "2031-02-20"}
    )
    await h.pick_slot(cb, state, _SP)
    assert _texts(cb.message.answer)[0] == messages.schedule.not_found


# --- card & delete ------------------------------------------------------------


async def test_show_card_renders(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Лиза")
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:card:{appt.id}")
    await h.show_card(cb, _SP)
    assert "Лиза" in _texts(cb.message.edit_text)[0]
    # No origin → back defaults to the appointment's own day.
    assert "sched:day_view:2030-01-15" in _callbacks(_markup(cb.message.edit_text))


async def test_show_card_back_returns_to_origin(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    # Opened from a client card → back returns there.
    cb = _fake_callback(f"sched:card:{appt.id}~clients:card:{client_id}")
    await h.show_card(cb, _SP)
    assert f"clients:card:{client_id}" in _callbacks(_markup(cb.message.edit_text))


async def test_show_card_uses_dash_for_missing_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    appt = await _seed_appt(session_factory, client_id=4242, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:card:{appt.id}")
    await h.show_card(cb, _SP)
    assert messages.schedule.dash in _texts(cb.message.edit_text)[0]


async def test_show_card_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:card:999")
    await h.show_card(cb, _SP)
    cb.answer.assert_awaited_once_with(messages.schedule.not_found, show_alert=True)
    cb.message.edit_text.assert_not_awaited()


async def test_confirm_then_do_delete(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)

    back = f"clients:card:{client_id}"
    confirm_cb = _fake_callback(f"sched:del:{appt.id}~{back}")
    await h.confirm_delete(confirm_cb)
    assert _texts(confirm_cb.message.edit_text)[0] == messages.schedule.confirm_delete
    # The confirm step threads the origin through to the irreversible action.
    assert f"sched:delyes:{appt.id}~{back}" in _callbacks(
        _markup(confirm_cb.message.edit_text)
    )

    del_cb = _fake_callback(f"sched:delyes:{appt.id}~{back}")
    await h.do_delete(del_cb, _state(), _SP)
    # The stale card becomes the standalone "deleted" result (buttons dropped)...
    assert _texts(del_cb.message.edit_text)[0] == messages.schedule.deleted
    # ...and the origin (client card) re-opens as a fresh message — no dead-end.
    card_cbs = _callbacks(_markup(del_cb.message.answer))
    assert f"clients:edit:{client_id}" in card_cbs
    async with session_factory() as session:
        assert appt.id is not None
        assert (
            await SqlAlchemyAppointmentsRepo(session).get_for_specialist(appt.id, _SP)
        ) is None


async def test_day_view_shows_virtual_recurring_occurrence(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    # A future slot occurrence renders as a button routing to its meeting card
    # (recur:occ:<slot>:<date>) — the virtual-occurrence branch of _appt_row.
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    now = datetime.now(UTC)
    today = today_in_tz(now, _TZ)
    days_ahead = (0 - today.weekday()) % 7 or 7  # the next Monday (a working day)
    monday = today + timedelta(days=days_ahead)
    async with session_factory() as session:
        schedule = await create_schedule(
            SqlAlchemyRecurringScheduleRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            comment=None,
            now=now,
        )
    assert schedule.id is not None
    async with session_factory() as session:
        slot = await add_slot(
            SqlAlchemyRecurringSlotRepo(session),
            schedule_id=schedule.id,
            weekday=0,
            time_hhmm="14:00",
            tz=_TZ,
            now=now,
            start_date=monday,
        )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:day_view:{monday.isoformat()}")
    await h.open_day(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert any(
        c is not None and c.startswith(f"recur:occ:{slot.id}:{monday.isoformat()}")
        for c in callbacks
    )


async def test_cancel_clears_state_and_opens_today(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(data={"flow": "create"}, state=Schedule.comment)
    await h.cancel(cb, state, _SP)
    assert state.store == {}
    # Cancel lands on today's (empty) day view.
    assert messages.schedule.day_empty in _texts(cb.message.edit_text)[0]


# --- day / week / history -----------------------------------------------------


async def test_show_feed_opens_today(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show_feed(msg, _SP)
    assert messages.schedule.day_empty in _texts(msg.answer)[0]
    cbs = _callbacks(_markup(msg.answer))
    assert any(c and c.startswith("sched:day_view:") for c in cbs)  # ◀/▶ nav
    assert "sched:week" in cbs


async def test_open_day_lists_that_days_appointments(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Аня")
    # 14:00 local (+05) on 2030-01-15 → 09:00 UTC.
    await _seed_appt(
        session_factory,
        client_id=client_id,
        starts_at=datetime(2030, 1, 15, 9, 0, tzinfo=UTC),
        comment="пробное занятие",
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-15")
    await h.open_day(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    assert "января" in text  # header: date + weekday
    # Appointments are buttons (time · child · comment), not a duplicated text list.
    labels = _button_texts(_markup(cb.message.edit_text))
    assert any(
        "Аня" in label and "14:00" in label and "пробное занятие" in label
        for label in labels
    )
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert "sched:day_view:2030-01-16" in cbs  # next day
    assert "sched:day_view:2030-01-14" in cbs  # previous day


async def test_open_day_today_via_feed_callback(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:feed")
    await h.open_day(cb, _SP)
    assert messages.schedule.day_empty in _texts(cb.message.edit_text)[0]


async def test_show_week_empty_and_populated(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)

    empty = _fake_callback("sched:week")
    await h.show_week(empty, _SP)
    assert _texts(empty.message.edit_text)[0] == messages.schedule.week_empty

    client_id = await _seed_client(session_factory, child="Дима")
    soon = datetime.now(UTC) + timedelta(days=2)  # within the coming week
    await _seed_appt(session_factory, client_id=client_id, starts_at=soon)
    populated = _fake_callback("sched:week")
    await h.show_week(populated, _SP)
    assert "Дима" in _texts(populated.message.edit_text)[0]
    assert "sched:feed" in _callbacks(_markup(populated.message.edit_text))


async def test_open_day_skips_empty_weekend_in_navigation(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)  # default working days Mon-Fri
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-18")  # Friday
    await h.open_day(cb, _SP)
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert "sched:day_view:2030-01-21" in cbs  # ▶ → Monday, empty weekend skipped
    assert "sched:day_view:2030-01-17" in cbs  # ◀ → Thursday
    assert "sched:day_view:2030-01-19" not in cbs  # empty Saturday is not a target


async def test_open_day_nonworking_day_with_appt_is_navigable(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    # Saturday 2030-01-19, 14:00 local (+05) → 09:00 UTC.
    await _seed_appt(
        session_factory,
        client_id=client_id,
        starts_at=datetime(2030, 1, 19, 9, 0, tzinfo=UTC),
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-18")  # Friday
    await h.open_day(cb, _SP)
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert "sched:day_view:2030-01-19" in cbs  # ▶ → Saturday with the appointment


async def test_open_day_hides_arrow_without_shown_neighbour(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            sp_id, {"working_days": ""}
        )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-18")  # no working days, no appts
    await h.open_day(cb, _SP)
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert not any(c and c.startswith("sched:day_view:") for c in cbs)  # arrows hidden
    assert "sched:week" in cbs  # week/history row still present


async def test_show_history_by_week(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Гриша")
    h = _handlers(messages, session_factory)

    # No history at all → bare empty message, no navigation.
    empty_cb = _fake_callback("sched:hist:0")
    await h.show_history(empty_cb, _SP)
    assert _texts(empty_cb.message.edit_text)[0] == messages.schedule.history_empty

    # Seed a record 10 days ago (a fully-past calendar week) + one in 2020.
    now = datetime.now(UTC)
    today = today_in_tz(now, _TZ)
    past_day = today - timedelta(days=10)
    monday_today = today - timedelta(days=today.weekday())
    monday_past = past_day - timedelta(days=past_day.weekday())
    week = (monday_today - monday_past).days // 7  # >= 1
    await _seed_appt(
        session_factory,
        client_id=client_id,
        starts_at=wall_to_utc(past_day, "14:00", _TZ),
    )
    await _seed_appt(session_factory, client_id=client_id, starts_at=_PAST)  # 2020

    # That week: grouped text with the child, both arrows (newer + older exist).
    cb = _fake_callback(f"sched:hist:{week}")
    await h.show_history(cb, _SP)
    assert "Гриша" in _texts(cb.message.edit_text)[0]
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert f"sched:hist:{week - 1}" in cbs  # ▶ newer week
    assert f"sched:hist:{week + 1}" in cbs  # ◀ older week

    # Current week (0): empty but older records exist → range title + ◀ only.
    week0 = _fake_callback("sched:hist:0")
    await h.show_history(week0, _SP)
    cbs0 = _callbacks(_markup(week0.message.edit_text))
    assert "sched:hist:1" in cbs0  # ◀ older
    assert "sched:hist:-1" not in cbs0  # nothing newer than the current week


async def test_navigate_and_noop(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    nav = _fake_callback("sched:cal:2030:5")
    await h.navigate(nav, _SP)
    nav.message.edit_reply_markup.assert_awaited_once()

    noop = _fake_callback("sched:noop")
    await h.noop(noop)
    noop.answer.assert_awaited_once()


# --- client history -----------------------------------------------------------


async def test_client_history_empty_and_populated(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Вася")
    h = _handlers(messages, session_factory)

    empty = _fake_callback(f"sched:chist:{client_id}:0")
    await h.show_client_history(empty, _SP)
    assert _texts(empty.message.edit_text)[0] == messages.schedule.client_history_empty

    await _seed_appt(session_factory, client_id=client_id, starts_at=_PAST)
    populated = _fake_callback(f"sched:chist:{client_id}:0")
    await h.show_client_history(populated, _SP)
    assert "Вася" in _texts(populated.message.edit_text)[0]


async def test_client_history_pagination_nav(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Гена")
    monkeypatch.setattr("src.bot.handlers.schedule._HISTORY_PAGE_SIZE", 1)
    await _seed_appt(session_factory, client_id=client_id, starts_at=_PAST)
    await _seed_appt(
        session_factory, client_id=client_id, starts_at=_PAST.replace(year=2019)
    )
    h = _handlers(messages, session_factory)
    cb0 = _fake_callback(f"sched:chist:{client_id}:0")
    await h.show_client_history(cb0, _SP)
    assert f"sched:chist:{client_id}:1" in _callbacks(
        _markup(cb0.message.edit_text)
    )  # ▶

    cb1 = _fake_callback(f"sched:chist:{client_id}:1")
    await h.show_client_history(cb1, _SP)
    assert f"sched:chist:{client_id}:0" in _callbacks(
        _markup(cb1.message.edit_text)
    )  # ◀


# --- confirmation status display ---------------------------------------------


async def _seed_reminder_status(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    starts_at: datetime,
    status: ReminderStatus,
) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        repo = SqlAlchemyRemindersRepo(session)
        reminder = AppointmentReminder(
            id=None,
            specialist_id=_SP,
            client_id=client_id,
            starts_at=starts_at,
            slot_id=None,
            origin_date=None,
            status=ReminderStatus.PENDING,
            sent_at=now,
            responded_at=None,
        )
        await repo.insert_pending(reminder)
        assert reminder.id is not None
        if status is not ReminderStatus.PENDING:
            await repo.set_status(reminder.id, status, now)


async def test_day_view_marks_confirmed_appointment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Аня")
    starts_at = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
    await _seed_appt(session_factory, client_id=client_id, starts_at=starts_at)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=starts_at,
        status=ReminderStatus.CONFIRMED,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-15")
    await h.open_day(cb, _SP)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert any(label.startswith(messages.reminder.confirmed_mark) for label in labels)


async def test_day_view_marks_declined_appointment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Аня")
    starts_at = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
    await _seed_appt(session_factory, client_id=client_id, starts_at=starts_at)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=starts_at,
        status=ReminderStatus.DECLINED,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-15")
    await h.open_day(cb, _SP)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert any(label.startswith(messages.reminder.declined_mark) for label in labels)


async def test_day_view_no_mark_for_pending(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Аня")
    starts_at = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
    await _seed_appt(session_factory, client_id=client_id, starts_at=starts_at)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=starts_at,
        status=ReminderStatus.PENDING,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:day_view:2030-01-15")
    await h.open_day(cb, _SP)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert not any(
        label.startswith(messages.reminder.confirmed_mark) for label in labels
    )


async def test_card_shows_confirmed_mark(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Лиза")
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=_FUTURE,
        status=ReminderStatus.CONFIRMED,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:card:{appt.id}")
    await h.show_card(cb, _SP)
    assert messages.reminder.card_confirmed in _texts(cb.message.edit_text)[0]


async def test_card_no_mark_when_declined(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, child="Лиза")
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=_FUTURE,
        status=ReminderStatus.DECLINED,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"sched:card:{appt.id}")
    await h.show_card(cb, _SP)
    assert messages.reminder.card_confirmed not in _texts(cb.message.edit_text)[0]


# --- notify the client: choose the send moment --------------------------------


def _all_callbacks(mock: AsyncMock) -> list[str | None]:
    # Flatten the inline-keyboard callbacks across every call of an answer mock.
    out: list[str | None] = []
    for call in mock.await_args_list:
        markup = call.kwargs.get("reply_markup")
        if markup is not None:
            out.extend(_callbacks(markup))
    return out


def _notify_data(
    *,
    client_id: int,
    chat_id: int = 555,
    event: str = "c",
    text: str = "Вы записаны.",
    target_key: str = "appt:1",
) -> dict[str, Any]:
    return {
        "notify": {
            "event": event,
            "client_id": client_id,
            "chat_id": chat_id,
            "text": text,
            "target_key": target_key,
            # The client card needs no missing entity to re-render after the flow.
            "card": f"clients:card:{client_id}",
        }
    }


async def _queued(factory: async_sessionmaker[AsyncSession], client_id: int) -> list:
    async with factory() as session:
        return await SqlAlchemyScheduledMessagesRepo(session).list_queued_for_client(
            _SP, client_id
        )


async def test_create_asks_to_notify_linked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",
            "hhmm": "14:00",
        }
    )
    await h.skip_comment(cb, state, _SP)
    # The last message is the Yes/No notify prompt; "Yes" reveals the moment choice.
    cbs = _callbacks(_markup(cb.message.answer))
    assert "sched:ntfwhen" in cbs
    assert "sched:ntfno" in cbs
    # The notify context is snapshotted in FSM for the moment sub-step.
    notify = state.store["notify"]
    assert notify["client_id"] == client_id
    assert notify["target_key"].startswith("appt:")
    wall = utc_to_wall(datetime(2030, 1, 15, 9, 0, tzinfo=UTC), _TZ)
    preview = messages.schedule.notify_created.format(
        date=format_ru_date(wall.date()), time=f"{wall:%H:%M}"
    )
    assert preview in _texts(cb.message.answer)[-1]
    assert preview == notify["text"]


async def test_create_does_not_ask_for_unlinked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)  # not linked to the bot
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",
            "hhmm": "14:00",
        }
    )
    await h.skip_comment(cb, state, _SP)
    # No notify prompt; the card is opened directly as the final screen.
    assert not any(
        c and c.startswith("sched:ntf") for c in _all_callbacks(cb.message.answer)
    )
    assert "notify" not in state.store


async def test_reschedule_asks_to_notify_linked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    state = _state(
        data={"flow": "reschedule", "appointment_id": appt.id, "day": "2031-02-20"}
    )
    slot_cb = _fake_callback("sched:slot:1000")
    await h.pick_slot(slot_cb, state, _SP)
    cbs = _callbacks(_markup(slot_cb.message.answer))
    assert "sched:ntfwhen" in cbs
    assert state.store["notify"]["event"] == "r"
    assert state.store["notify"]["target_key"] == f"appt:{appt.id}"


async def test_delete_asks_to_notify_linked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    state = _state()
    del_cb = _fake_callback(f"sched:delyes:{appt.id}~clients:card:{client_id}")
    await h.do_delete(del_cb, state, _SP)
    # "Deleted" is the edited message; the notify prompt is a separate answer.
    assert _texts(del_cb.message.edit_text)[0] == messages.schedule.deleted
    cbs = _callbacks(_markup(del_cb.message.answer))
    assert "sched:ntfwhen" in cbs
    # The target is the appointment id, captured before the row was deleted.
    assert state.store["notify"]["target_key"] == f"appt:{appt.id}"


async def test_delete_does_not_ask_for_unlinked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)  # not linked
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    del_cb = _fake_callback(f"sched:delyes:{appt.id}~clients:card:{client_id}")
    await h.do_delete(del_cb, _state(), _SP)
    # Only the card auto-returns — no separate notify prompt for an unlinked client.
    del_cb.message.answer.assert_awaited_once()


async def test_delete_missing_appointment_does_not_ask(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    del_cb = _fake_callback("sched:delyes:999~sched:feed")
    await h.do_delete(del_cb, _state(), _SP)
    # Idempotent delete of a gone appointment still auto-returns, but never asks.
    del_cb.message.answer.assert_awaited_once()


async def test_ask_when_shows_moment_choice(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfwhen")
    state = _state(data=_notify_data(client_id=client_id))
    await h.ask_when(cb, state, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_when_ask
    cbs = _callbacks(_markup(cb.message.edit_text))
    assert {"sched:ntfnow", "sched:ntfpreset", "sched:ntfcustom"} <= set(cbs)
    # The preset button carries the specialist's deferred_notify_time (default 20:00).
    assert any("20:00" in t for t in _button_texts(_markup(cb.message.edit_text)))


async def test_ask_when_stale_without_context(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfwhen")
    await h.ask_when(cb, _state(), _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_session_stale


async def test_notify_now_sends_for_each_event(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    for event in ("c", "r", "x"):
        cb = _fake_callback("sched:ntfnow")
        state = _state(
            data=_notify_data(
                client_id=client_id, event=event, text="Текст уведомления."
            )
        )
        await h.notify_now(cb, state, _SP)
        cb.bot.send_message.assert_awaited_once()
        assert cb.bot.send_message.await_args.args == (555, "Текст уведомления.")
        assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_sent
        # The card re-opens as the freshest screen, and FSM is cleared.
        cb.message.answer.assert_awaited_once()
        assert "notify" not in state.store


async def test_notify_now_failed_on_forbidden(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfnow")
    cb.bot.send_message.side_effect = TelegramForbiddenError(
        method=None,  # type: ignore[arg-type]
        message="blocked",
    )
    await h.notify_now(cb, _state(data=_notify_data(client_id=client_id)), _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_failed


async def test_notify_now_bad_request(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfnow")
    cb.bot.send_message.side_effect = TelegramBadRequest(
        method=None,  # type: ignore[arg-type]
        message="chat not found",
    )
    await h.notify_now(cb, _state(data=_notify_data(client_id=client_id)), _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_failed


async def test_notify_now_not_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)  # never linked
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfnow")
    await h.notify_now(cb, _state(data=_notify_data(client_id=client_id)), _SP)
    cb.bot.send_message.assert_not_awaited()
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_not_linked


async def test_notify_now_stale(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfnow")
    await h.notify_now(cb, _state(), _SP)
    cb.bot.send_message.assert_not_awaited()
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_session_stale


async def test_notify_now_records_audit(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfnow")
    await h.notify_now(
        cb, _state(data=_notify_data(client_id=client_id, event="m")), _SP
    )
    assert await _audit_messages(session_factory) == [
        (AuditEvent.NOTIFY_RESCHEDULED, DeliveryStatus.SENT)
    ]


async def test_notify_preset_queues(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfpreset")
    state = _state(
        data=_notify_data(client_id=client_id, text="Вы записаны.", target_key="appt:7")
    )
    await h.notify_preset(cb, state, _SP)
    # Nothing is sent now; a queued row is created and the choice is confirmed.
    cb.bot.send_message.assert_not_awaited()
    rows = await _queued(session_factory, client_id)
    assert len(rows) == 1
    assert rows[0].text == "Вы записаны."
    assert rows[0].target_key == "appt:7"
    assert rows[0].event is AuditEvent.NOTIFY_CREATED
    assert (
        messages.schedule.notify_deferred_queued.split("{")[0]
        in (_texts(cb.message.edit_text)[0])
    )
    assert "notify" not in state.store


async def test_notify_preset_supersede_announced(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    for _ in range(2):
        cb = _fake_callback("sched:ntfpreset")
        await h.notify_preset(
            cb, _state(data=_notify_data(client_id=client_id, target_key="appt:7")), _SP
        )
        last = cb
    rows = await _queued(session_factory, client_id)
    assert len(rows) == 1  # the first row was superseded
    head = messages.schedule.notify_deferred_superseded.split("{")[0]
    assert head in _texts(last.message.edit_text)[0]


async def test_notify_preset_stale(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfpreset")
    await h.notify_preset(cb, _state(), _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_session_stale


async def test_notify_custom_prompts_then_queues(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    state = _state(data=_notify_data(client_id=client_id, target_key="appt:9"))
    ask_cb = _fake_callback("sched:ntfcustom")
    await h.notify_custom(ask_cb, state)
    assert state.state is Schedule.notify_custom_time
    assert (
        _texts(ask_cb.message.edit_text)[0] == messages.schedule.notify_custom_time_ask
    )
    msg = _fake_message("20:00")
    await h.apply_notify_custom_time(msg, state, _SP)
    rows = await _queued(session_factory, client_id)
    assert len(rows) == 1
    assert rows[0].target_key == "appt:9"


async def test_notify_custom_invalid_time(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    state = _state(
        data=_notify_data(client_id=client_id), state=Schedule.notify_custom_time
    )
    msg = _fake_message("четверть восьмого")
    await h.apply_notify_custom_time(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.schedule.bad_time
    assert await _queued(session_factory, client_id) == []


async def test_notify_custom_stale(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfcustom")
    await h.notify_custom(cb, _state())
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_session_stale


async def test_notify_skip_declines_and_returns_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfno")
    state = _state(data=_notify_data(client_id=client_id))
    await h.notify_skip(cb, state, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_skipped
    cb.bot.send_message.assert_not_awaited()
    # The entity card is re-opened as the freshest screen.
    cb.message.answer.assert_awaited_once()
    assert "notify" not in state.store


async def test_notify_skip_without_context(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback("sched:ntfno")
    await h.notify_skip(cb, _state(), _SP)
    assert _texts(cb.message.edit_text)[0] == messages.schedule.notify_skipped
    cb.message.answer.assert_not_awaited()


async def _audit_messages(
    factory: async_sessionmaker[AsyncSession],
) -> list[tuple[AuditEvent, DeliveryStatus | None]]:
    async with factory() as session:
        rows = await SqlAlchemyAuditRepo(session).list_for_specialist(
            _SP, limit=50, offset=0
        )
    return [(r.event, r.status) for r in rows if r.kind is AuditKind.MESSAGE]


# --- auto-return after irreversible actions -----------------------------------


async def test_delete_from_day_returns_to_same_day(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    day = utc_to_wall(_FUTURE, _TZ).date()
    del_cb = _fake_callback(f"sched:delyes:{appt.id}~sched:day_view:{day.isoformat()}")
    await h.do_delete(del_cb, _state(), _SP)
    assert _texts(del_cb.message.edit_text)[0] == messages.schedule.deleted
    # The same day re-opens as the freshest screen (no dead-end "Back").
    assert format_ru_date(day) in _texts(del_cb.message.answer)[0]


async def test_nav_client_history_renders_history(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    text, _ = await h.nav_client_history(_SP, f"sched:chist:{client_id}:0")
    assert text == messages.schedule.client_history_empty


async def test_nav_appt_card_defaults_back_to_day(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    h = _handlers(messages, session_factory)
    # No "~inner" → back defaults to the appointment's own day.
    _, keyboard = await h.nav_appt_card(_SP, f"sched:card:{appt.id}")
    day = utc_to_wall(_FUTURE, _TZ).date()
    assert keyboard.inline_keyboard[-1][0].callback_data == (
        f"sched:day_view:{day.isoformat()}"
    )


async def test_nav_appt_card_marks_confirmed(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    appt = await _seed_appt(session_factory, client_id=client_id, starts_at=_FUTURE)
    await _seed_reminder_status(
        session_factory,
        client_id=client_id,
        starts_at=_FUTURE,
        status=ReminderStatus.CONFIRMED,
    )
    h = _handlers(messages, session_factory)
    text, _ = await h.nav_appt_card(
        _SP, f"sched:card:{appt.id}~clients:card:{client_id}"
    )
    assert messages.reminder.card_confirmed in text
