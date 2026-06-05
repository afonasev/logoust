from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.schedule import (
    Schedule,
    ScheduleHandlers,
    build_calendar,
    build_slots_keyboard,
    render_card,
)
from src.bot.messages import BotMessages
from src.domain.appointment import Appointment
from src.domain.schedule import today_in_tz, wall_to_utc
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import SqlAlchemyRecurringRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

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
    return ScheduleHandlers(messages, factory)


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
    assert state.store["regular"] is False
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


async def test_regular_flow_creates_series(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("каждую неделю")
    state = _state(
        data={
            "flow": "create",
            "client_id": client_id,
            "day": "2030-01-15",  # a Tuesday
            "hhmm": "14:00",
            "regular": True,
        }
    )
    await h.apply_comment(msg, state, _SP)
    # The regular branch creates a series (not a one-off) and shows its card.
    assert messages.recurring.created in _texts(msg.answer)
    async with session_factory() as session:
        series = await SqlAlchemyRecurringRepo(session).list_active_for_specialist(_SP)
    assert len(series) == 1
    assert series[0].start_date == date(2030, 1, 15)
    assert series[0].weekday == date(2030, 1, 15).weekday()
    assert series[0].time_hhmm == "14:00"
    assert series[0].comment == "каждую неделю"


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
    assert state.store == {"flow": "reschedule", "appointment_id": appt.id}

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
    await h.do_delete(del_cb, _SP)
    assert _texts(del_cb.message.edit_text)[0] == messages.schedule.deleted
    # After deletion the back button returns to the card's origin, not the feed.
    assert _callbacks(_markup(del_cb.message.edit_text)) == [back]
    async with session_factory() as session:
        assert appt.id is not None
        assert (
            await SqlAlchemyAppointmentsRepo(session).get_for_specialist(appt.id, _SP)
        ) is None


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
