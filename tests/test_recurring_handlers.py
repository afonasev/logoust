from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.schedule import (
    RecurringHandlers,
    ScheduleHandlers,
    _build_navigator,
)
from src.bot.messages import BotMessages
from src.domain.recurring import RecurringSchedule, RecurringSlot
from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.schedule import today_in_tz, wall_to_utc
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

_SP = 1
_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST (matches the specialist default)
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


async def _seed_other_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        other = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert other.id is not None
    assert other.id != _SP
    return other.id


async def _seed_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int | None = None
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
        if chat_id is not None:
            await repo.link_telegram(
                client.id,
                telegram_chat_id=chat_id,
                username=None,
                linked_at=now,
                updated_at=now,
            )
    return client.id


async def _seed_schedule(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    comment: str | None = "регулярная",
    active: bool = True,
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        schedule = await SqlAlchemyRecurringScheduleRepo(session).add(
            RecurringSchedule(
                id=None,
                specialist_id=_SP,
                client_id=client_id,
                comment=comment,
                active=active,
                created_at=now,
                updated_at=now,
            )
        )
    assert schedule.id is not None
    return schedule.id


async def _seed_slot(  # noqa: PLR0913
    factory: async_sessionmaker[AsyncSession],
    *,
    schedule_id: int,
    weekday: int,
    start_date: date,
    time_hhmm: str = "14:00",
    active: bool = True,
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        slot = await SqlAlchemyRecurringSlotRepo(session).add(
            RecurringSlot(
                id=None,
                schedule_id=schedule_id,
                weekday=weekday,
                time_hhmm=time_hhmm,
                active=active,
                start_date=start_date,
                materialized_through=start_date,
                created_at=now,
                updated_at=now,
            )
        )
    assert slot.id is not None
    return slot.id


async def _upsert_override(  # noqa: PLR0913
    factory: async_sessionmaker[AsyncSession],
    *,
    slot_id: int,
    original_date: date,
    skipped: bool = False,
    moved_to: datetime | None = None,
    comment: str | None = None,
) -> None:
    async with factory() as session:
        await SqlAlchemyRecurringSlotOverrideRepo(session).upsert(
            slot_id,
            original_date,
            skipped=skipped,
            moved_to=moved_to,
            comment=comment,
            created_at=datetime.now(UTC),
        )


def _recur(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> RecurringHandlers:
    h = ScheduleHandlers(messages, factory)
    r = RecurringHandlers(messages, factory)
    r.set_navigator(_build_navigator(messages, factory, h, r))
    return r


async def _load_schedule(
    factory: async_sessionmaker[AsyncSession], schedule_id: int
) -> RecurringSchedule | None:
    async with factory() as session:
        return await SqlAlchemyRecurringScheduleRepo(session).get_for_specialist(
            schedule_id, _SP
        )


async def _load_slots(
    factory: async_sessionmaker[AsyncSession], schedule_id: int
) -> list[RecurringSlot]:
    async with factory() as session:
        return await SqlAlchemyRecurringSlotRepo(session).list_for_schedule(schedule_id)


async def _list_overrides(
    factory: async_sessionmaker[AsyncSession], slot_id: int
) -> list[Any]:
    async with factory() as session:
        return await SqlAlchemyRecurringSlotOverrideRepo(session).list_for_slot(slot_id)


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


# --- creation wizard --------------------------------------------------------


async def test_add_day_shows_weekday_picker(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _recur(messages, session_factory)
    cb = _fake_callback("recur:add")
    await h.add_day(cb)
    assert messages.recurring.pick_weekday in _texts(cb.message.edit_text)
    assert "recur:wd:0" in _callbacks(_markup(cb.message.edit_text))


async def test_create_wizard_second_slot_then_finish(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    first_start = _next_weekday(today, 0)
    h = _recur(messages, session_factory)
    # Seeded as if choose_regular(sched:reg:1) already created the first slot.
    state = _state(
        {
            "flow": "rcreate",
            "client_id": client_id,
            "slots": [
                {
                    "weekday": 0,
                    "hhmm": "12:00",
                    "start_date": first_start.isoformat(),
                }
            ],
        }
    )
    # Add a second day (Wednesday) at 14:00.
    await h.add_day(_fake_callback("recur:add"))
    await h.pick_weekday(_fake_callback("recur:wd:2"), state, _SP)
    add_cb = _fake_callback("recur:tslot:1400")
    await h.pick_slot(add_cb, state, _SP)
    # _append_slot answers with the "add more?" prompt.
    assert messages.recurring.add_more in _texts(add_cb.message.answer)
    assert len(state.store["slots"]) == 2

    done = _fake_callback("recur:done")
    await h.done_adding(done, state)
    assert messages.recurring.ask_comment in _texts(done.message.edit_text)

    msg = _fake_message("принести тетрадь")
    await h.apply_schedule_comment(msg, state, _SP)
    assert messages.recurring.created in _texts(msg.answer)

    schedules = await _load_schedules(session_factory)
    assert len(schedules) == 1
    schedule_id = schedules[0].id
    assert schedule_id is not None
    assert schedules[0].comment == "принести тетрадь"
    slots = await _load_slots(session_factory, schedule_id)
    assert {(s.weekday, s.time_hhmm) for s in slots} == {(0, "12:00"), (2, "14:00")}
    assert state.store == {}


async def test_create_wizard_custom_time_appends_slot(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    h = _recur(messages, session_factory)
    state = _state(
        {
            "flow": "rcreate",
            "client_id": client_id,
            "slots": [
                {
                    "weekday": 0,
                    "hhmm": "12:00",
                    "start_date": _next_weekday(today, 0).isoformat(),
                }
            ],
        }
    )
    await h.pick_weekday(_fake_callback("recur:wd:4"), state, _SP)
    await h.ask_custom_time(_fake_callback("recur:tother"), state)
    msg = _fake_message("10:30")
    await h.apply_custom_time(msg, state, _SP)
    assert messages.recurring.add_more in _texts(msg.answer)
    assert state.store["slots"][1] == {
        "weekday": 4,
        "hhmm": "10:30",
        "start_date": _next_weekday(today, 4).isoformat(),
    }


async def test_create_wizard_bad_custom_time_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _recur(messages, session_factory)
    state = _state({"flow": "rcreate", "client_id": 1, "slots": [], "weekday": 0})
    msg = _fake_message("99:99")
    await h.apply_custom_time(msg, state, _SP)
    assert messages.recurring.bad_time in _texts(msg.answer)


async def test_skip_schedule_comment_creates_without_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    h = _recur(messages, session_factory)
    state = _state(
        {
            "flow": "rcreate",
            "client_id": client_id,
            "slots": [
                {
                    "weekday": 1,
                    "hhmm": "15:00",
                    "start_date": _next_weekday(today, 1).isoformat(),
                }
            ],
        }
    )
    cb = _fake_callback("recur:cskipc")
    await h.skip_schedule_comment(cb, state, _SP)
    assert messages.recurring.created in _texts(cb.message.answer)
    schedules = await _load_schedules(session_factory)
    assert len(schedules) == 1
    assert schedules[0].comment is None


async def test_create_asks_series_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    today = today_in_tz(datetime.now(UTC), _TZ)
    h = _recur(messages, session_factory)
    state = _state(
        {
            "flow": "rcreate",
            "client_id": client_id,
            "slots": [
                {
                    "weekday": 0,
                    "hhmm": "12:00",
                    "start_date": _next_weekday(today, 0).isoformat(),
                }
            ],
        }
    )
    cb = _fake_callback("recur:cskipc")
    await h.skip_schedule_comment(cb, state, _SP)
    schedules = await _load_schedules(session_factory)
    schedule_id = schedules[0].id
    assert "sched:ntfwhen" in _callbacks(_markup(cb.message.answer))
    assert state.store["notify"]["event"] == "c"
    assert state.store["notify"]["target_key"] == f"schedule:{schedule_id}"


# --- schedule card ----------------------------------------------------------


async def test_show_schedule_renders_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_mon = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=_next_weekday(today, 0),
        time_hhmm="12:00",
    )
    slot_wed = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=2,
        start_date=_next_weekday(today, 2),
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    back = f"clients:card:{client_id}"
    cb = _fake_callback(f"recur:sched:{schedule_id}~{back}")
    await h.show_schedule(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    assert "Петя" in text
    assert "регулярная" in text
    # Both slot rules are rendered (rule_line uses the "Каждый…" weekday form).
    assert "Каждый понедельник в 12:00" in text
    assert "Каждую среду в 14:00" in text
    callbacks = _callbacks(_markup(cb.message.edit_text))
    # Occurrence buttons for both slots within the 14-day window.
    assert any(c and c.startswith(f"recur:occ:{slot_mon}:") for c in callbacks)
    assert any(c and c.startswith(f"recur:occ:{slot_wed}:") for c in callbacks)
    assert f"recur:cfg:{schedule_id}" in callbacks
    assert f"recur:stopask:{schedule_id}~{back}" in callbacks
    # The back button honours the origin we came from.
    assert back in callbacks


async def test_show_schedule_empty_window(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    # Start far in the future so no occurrence lands in the rolling 14-day window.
    await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=today + timedelta(days=30),
        time_hhmm="12:00",
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:sched:{schedule_id}")
    await h.show_schedule(cb, _SP)
    assert messages.recurring.empty_window in _texts(cb.message.edit_text)[0]
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert not any(c and c.startswith("recur:occ:") for c in callbacks)


async def test_show_schedule_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:sched:{schedule_id}")
    await h.show_schedule(cb, other)
    cb.message.edit_text.assert_not_awaited()
    cb.answer.assert_awaited()


async def test_nav_schedule_card_parses_composite(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=_next_weekday(today, 0),
    )
    h = _recur(messages, session_factory)
    back = f"recur:sched:{schedule_id}~clients:card:{client_id}"
    _, keyboard = await h.nav_schedule_card(_SP, back)
    cbs = _callbacks(keyboard)
    assert f"recur:cfg:{schedule_id}" in cbs
    assert f"clients:card:{client_id}" in cbs


# --- meeting card -----------------------------------------------------------


async def test_show_meeting_renders_occurrence(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=occ_day,
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    back = f"recur:sched:{schedule_id}"
    cb = _fake_callback(f"recur:occ:{slot_id}:{occ_day.isoformat()}~{back}")
    await h.show_meeting(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    assert "Петя" in text
    assert "14:00" in text
    # The schedule's shared comment is inherited.
    assert "регулярная" in text
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert any(c and c.startswith(f"recur:occmove:{slot_id}:") for c in callbacks)
    assert any(c and c.startswith(f"recur:occskipask:{slot_id}:") for c in callbacks)
    assert any(c and c.startswith(f"recur:occcmt:{slot_id}:") for c in callbacks)
    assert any(c and c.startswith(f"recur:sched:{schedule_id}") for c in callbacks)
    assert back in callbacks


async def test_show_meeting_marks_confirmed(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=occ_day,
        time_hhmm="14:00",
    )
    now = datetime.now(UTC)
    async with session_factory() as session:
        await SqlAlchemyRemindersRepo(session).insert_pending(
            AppointmentReminder(
                id=None,
                specialist_id=_SP,
                client_id=client_id,
                starts_at=wall_to_utc(occ_day, "14:00", _TZ),
                slot_id=slot_id,
                origin_date=occ_day,
                status=ReminderStatus.CONFIRMED,
                sent_at=now,
                responded_at=now,
            )
        )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:occ:{slot_id}:{occ_day.isoformat()}")
    await h.show_meeting(cb, _SP)
    assert messages.reminder.card_confirmed in _texts(cb.message.edit_text)[0]


async def test_show_meeting_moved_override_shows_new_time(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=occ_day,
        time_hhmm="14:00",
    )
    # Move to occ_day+2 at 16:00 local (11:00 UTC) with an override comment.
    moved_day = occ_day + timedelta(days=2)
    await _upsert_override(
        session_factory,
        slot_id=slot_id,
        original_date=occ_day,
        moved_to=wall_to_utc(moved_day, "16:00", _TZ),
        comment="перенос",
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:occ:{slot_id}:{occ_day.isoformat()}")
    await h.show_meeting(cb, _SP)
    text = _texts(cb.message.edit_text)[0]
    assert "16:00" in text
    # Override comment wins over the schedule's shared comment.
    assert "перенос" in text


async def test_show_meeting_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:occ:{slot_id}:2026-06-22")
    await h.show_meeting(cb, other)
    cb.message.edit_text.assert_not_awaited()


async def test_nav_meeting_card_builds_from_back(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    h = _recur(messages, session_factory)
    back = f"recur:occ:{slot_id}:{occ_day.isoformat()}~recur:sched:{schedule_id}"
    text, keyboard = await h.nav_meeting_card(_SP, back)
    assert "Петя" in text
    cbs = _callbacks(keyboard)
    assert any(c and c.startswith(f"recur:occmove:{slot_id}:") for c in cbs)
    assert f"recur:sched:{schedule_id}" in cbs


# --- configure --------------------------------------------------------------


async def test_show_config_lists_slots(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_a = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=date(2026, 6, 1),
        time_hhmm="12:00",
    )
    slot_b = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=2,
        start_date=date(2026, 6, 3),
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:cfg:{schedule_id}")
    await h.show_config(cb, _SP)
    assert messages.recurring.configure_title in _texts(cb.message.edit_text)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert f"recur:slot:{slot_a}" in callbacks
    assert f"recur:slot:{slot_b}" in callbacks
    assert f"recur:cfgadd:{schedule_id}" in callbacks
    assert f"recur:sched:{schedule_id}" in callbacks


async def test_show_config_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:cfg:{schedule_id}")
    await h.show_config(cb, other)
    cb.message.edit_text.assert_not_awaited()


async def test_show_slot_actions(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=date(2026, 6, 1),
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slot:{slot_id}")
    await h.show_slot_actions(cb, _SP)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert f"recur:slottime:{slot_id}" in callbacks
    assert f"recur:slotday:{slot_id}" in callbacks
    assert f"recur:slotdel:{slot_id}" in callbacks
    assert f"recur:cfg:{schedule_id}" in callbacks


async def test_show_slot_actions_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slot:{slot_id}")
    await h.show_slot_actions(cb, other)
    cb.message.edit_text.assert_not_awaited()


async def test_edit_slot_time_only(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    start = _next_weekday(today, 0)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=start,
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_slot_time(_fake_callback(f"recur:slottime:{slot_id}"), state, _SP)
    assert state.store["flow"] == "cfg_time"
    msg_cb = _fake_callback("recur:tslot:1600")
    await h.pick_slot(msg_cb, state, _SP)
    # Time-only edit keeps the grid: weekday and start_date untouched.
    slots = await _load_slots(session_factory, schedule_id)
    assert len(slots) == 1
    assert slots[0].time_hhmm == "16:00"
    assert slots[0].weekday == 0
    assert slots[0].start_date == start


async def test_edit_slot_day_change(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=_next_weekday(today, 0),
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_slot_day(_fake_callback(f"recur:slotday:{slot_id}"), state, _SP)
    assert state.store["flow"] == "cfg_day"
    await h.pick_weekday(_fake_callback("recur:wd:3"), state, _SP)  # Thursday
    msg_cb = _fake_callback("recur:tslot:1100")
    await h.pick_slot(msg_cb, state, _SP)
    assert messages.recurring.edited in _texts(msg_cb.message.answer)
    slots = await _load_slots(session_factory, schedule_id)
    assert slots[0].weekday == 3
    assert slots[0].time_hhmm == "11:00"
    # Weekday change recomputes start_date to the next Thursday ≥ today.
    assert slots[0].start_date == _next_weekday(today, 3)


async def test_edit_slot_foreign_reports_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    # State as if start_slot_time ran, but the edit is finished by a non-owner.
    state = _state({"flow": "cfg_time", "slot_id": slot_id, "weekday": 0})
    msg_cb = _fake_callback("recur:tslot:1600")
    await h.pick_slot(msg_cb, state, other)
    assert messages.recurring.not_found in _texts(msg_cb.message.answer)


async def test_start_slot_time_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slottime:{slot_id}")
    await h.start_slot_time(cb, _state(), other)
    cb.message.edit_text.assert_not_awaited()


async def test_start_slot_day_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slotday:{slot_id}")
    await h.start_slot_day(cb, _state(), other)
    cb.message.edit_text.assert_not_awaited()


async def test_add_slot_via_config(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=date(2026, 6, 1),
        time_hhmm="12:00",
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_cfg_add(_fake_callback(f"recur:cfgadd:{schedule_id}"), state, _SP)
    assert state.store["flow"] == "cfg_add"
    await h.pick_weekday(_fake_callback("recur:wd:4"), state, _SP)  # Friday
    msg_cb = _fake_callback("recur:tslot:1500")
    await h.pick_slot(msg_cb, state, _SP)
    assert messages.recurring.edited in _texts(msg_cb.message.answer)
    slots = await _load_slots(session_factory, schedule_id)
    assert {(s.weekday, s.time_hhmm) for s in slots} == {(0, "12:00"), (4, "15:00")}


async def test_start_cfg_add_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:cfgadd:{schedule_id}")
    await h.start_cfg_add(cb, _state(), other)
    cb.message.edit_text.assert_not_awaited()


async def test_delete_slot_keeps_schedule_with_remaining(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_keep = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=date(2026, 6, 1),
        time_hhmm="12:00",
    )
    slot_del = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=2,
        start_date=date(2026, 6, 3),
        time_hhmm="14:00",
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slotdel:{slot_del}")
    await h.delete_slot(cb, _SP)
    # Re-renders the configure list (one slot remains).
    assert messages.recurring.configure_title in _texts(cb.message.edit_text)
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert f"recur:slot:{slot_keep}" in callbacks
    assert f"recur:slot:{slot_del}" not in callbacks
    schedule = await _load_schedule(session_factory, schedule_id)
    assert schedule is not None
    assert schedule.active is True


async def test_delete_last_slot_stops_schedule(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slotdel:{slot_id}")
    await h.delete_slot(cb, _SP)
    # The slot is deactivated and, being the last active one, stops the schedule.
    schedule = await _load_schedule(session_factory, schedule_id)
    assert schedule is not None
    assert schedule.active is False
    assert await _load_slots(session_factory, schedule_id) == []
    # Last active slot removed → the handler reports "slot removed" and returns to
    # the freshest screen via the navigator (no empty configure re-render).
    assert messages.recurring.slot_removed in _texts(cb.message.edit_text)
    cb.message.answer.assert_awaited()


async def test_delete_slot_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:slotdel:{slot_id}")
    await h.delete_slot(cb, other)
    cb.message.edit_text.assert_not_awaited()
    slots = await _load_slots(session_factory, schedule_id)
    assert len(slots) == 1  # untouched


# --- stop the whole schedule ------------------------------------------------


async def test_stop_flow_confirms_and_deactivates(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    confirm = _fake_callback(f"recur:stopask:{schedule_id}")
    await h.confirm_stop(confirm, _SP)
    assert messages.recurring.confirm_stop in _texts(confirm.message.edit_text)
    assert f"recur:stop:{schedule_id}" in _callbacks(_markup(confirm.message.edit_text))

    do = _fake_callback(f"recur:stop:{schedule_id}")
    await h.do_stop(do, _state(), _SP)
    assert messages.recurring.stopped in _texts(do.message.edit_text)
    schedule = await _load_schedule(session_factory, schedule_id)
    assert schedule is not None
    assert schedule.active is False


async def test_stop_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    await h.confirm_stop(_fake_callback(f"recur:stopask:{schedule_id}"), other)
    await h.do_stop(_fake_callback(f"recur:stop:{schedule_id}"), _state(), other)
    schedule = await _load_schedule(session_factory, schedule_id)
    assert schedule is not None
    assert schedule.active is True


async def test_stop_asks_series_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    do = _fake_callback(f"recur:stop:{schedule_id}")
    state = _state()
    await h.do_stop(do, state, _SP)
    assert messages.recurring.stopped in _texts(do.message.edit_text)
    assert "sched:ntfwhen" in _callbacks(_markup(do.message.answer))
    assert state.store["notify"]["event"] == "x"
    assert state.store["notify"]["target_key"] == f"schedule:{schedule_id}"


# --- move a single occurrence -----------------------------------------------


async def test_move_occurrence_creates_override(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=occ_day,
        time_hhmm="14:00",
    )
    new_day = occ_day + timedelta(days=2)
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_move(
        _fake_callback(f"recur:occmove:{slot_id}:{occ_day.isoformat()}"), state, _SP
    )
    assert state.store["flow"] == "move"
    await h.pick_move_day(
        _fake_callback(f"recur:day:{new_day.year}:{new_day.month}:{new_day.day}"),
        state,
        _SP,
    )
    do = _fake_callback("recur:tslot:1600")
    await h.pick_slot(do, state, _SP)
    assert messages.recurring.moved in _texts(do.message.edit_text)
    overrides = await _list_overrides(session_factory, slot_id)
    assert len(overrides) == 1
    assert overrides[0].original_date == occ_day
    assert overrides[0].moved_to == wall_to_utc(new_day, "16:00", _TZ)
    assert overrides[0].skipped is False


async def test_move_via_navigate_then_typed_time(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory,
        schedule_id=schedule_id,
        weekday=0,
        start_date=occ_day,
        time_hhmm="14:00",
    )
    new_day = occ_day + timedelta(days=2)
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_move(
        _fake_callback(f"recur:occmove:{slot_id}:{occ_day.isoformat()}"), state, _SP
    )
    # Redraw the calendar to another month, then come back and pick a day.
    nav = _fake_callback("recur:cal:2027:3")
    await h.navigate_move(nav, _SP)
    nav_cbs = _callbacks(_markup(nav.message.edit_reply_markup))
    assert any(c and c.startswith("recur:day:2027:3:") for c in nav_cbs)

    await h.pick_move_day(
        _fake_callback(f"recur:day:{new_day.year}:{new_day.month}:{new_day.day}"),
        state,
        _SP,
    )
    await h.ask_custom_time(_fake_callback("recur:tother"), state)
    msg = _fake_message("16:00")
    await h.apply_custom_time(msg, state, _SP)
    # Typed time → result arrives as a fresh answer (edit=False).
    assert messages.recurring.moved in _texts(msg.answer)
    overrides = await _list_overrides(session_factory, slot_id)
    assert overrides[0].moved_to == wall_to_utc(new_day, "16:00", _TZ)


async def test_start_move_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:occmove:{slot_id}:2026-06-22")
    await h.start_move(cb, _state(), other)
    cb.message.edit_text.assert_not_awaited()


async def test_move_finished_by_non_owner_reports_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state(
        {
            "flow": "move",
            "slot_id": slot_id,
            "origin_date": "2026-06-22",
            "new_day": "2026-06-24",
            "back": "",
            "card": f"recur:occ:{slot_id}:2026-06-22",
        }
    )
    msg_cb = _fake_callback("recur:tslot:1600")
    await h.pick_slot(msg_cb, state, other)
    assert messages.recurring.not_found in _texts(msg_cb.message.answer)
    assert await _list_overrides(session_factory, slot_id) == []


async def test_move_asks_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    new_day = occ_day + timedelta(days=2)
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_move(
        _fake_callback(f"recur:occmove:{slot_id}:{occ_day.isoformat()}"), state, _SP
    )
    await h.pick_move_day(
        _fake_callback(f"recur:day:{new_day.year}:{new_day.month}:{new_day.day}"),
        state,
        _SP,
    )
    do = _fake_callback("recur:tslot:1600")
    await h.pick_slot(do, state, _SP)
    assert "sched:ntfwhen" in _callbacks(_markup(do.message.answer))
    assert state.store["notify"]["event"] == "r"
    assert state.store["notify"]["target_key"] == (
        f"slot:{slot_id}:{occ_day.isoformat()}"
    )


# --- skip a single occurrence -----------------------------------------------


async def test_skip_occurrence_creates_override(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    h = _recur(messages, session_factory)
    confirm = _fake_callback(f"recur:occskipask:{slot_id}:{occ_day.isoformat()}")
    await h.confirm_skip(confirm, _SP)
    assert f"recur:occskip:{slot_id}:{occ_day.isoformat()}" in _callbacks(
        _markup(confirm.message.edit_text)
    )
    do = _fake_callback(f"recur:occskip:{slot_id}:{occ_day.isoformat()}")
    await h.do_skip(do, _state(), _SP)
    assert messages.recurring.skipped in _texts(do.message.edit_text)
    overrides = await _list_overrides(session_factory, slot_id)
    assert len(overrides) == 1
    assert overrides[0].skipped is True
    assert overrides[0].moved_to is None


async def test_confirm_skip_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    await h.confirm_skip(
        _fake_callback(f"recur:occskipask:{slot_id}:2026-06-22"), other
    )
    await h.do_skip(
        _fake_callback(f"recur:occskip:{slot_id}:2026-06-22"), _state(), other
    )
    assert await _list_overrides(session_factory, slot_id) == []


async def test_skip_asks_notify_for_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    h = _recur(messages, session_factory)
    do = _fake_callback(f"recur:occskip:{slot_id}:{occ_day.isoformat()}")
    state = _state()
    await h.do_skip(do, state, _SP)
    assert "sched:ntfwhen" in _callbacks(_markup(do.message.answer))
    assert state.store["notify"]["event"] == "x"
    assert state.store["notify"]["target_key"] == (
        f"slot:{slot_id}:{occ_day.isoformat()}"
    )


# --- comment a single occurrence --------------------------------------------


async def test_comment_occurrence_sets_override_comment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    h = _recur(messages, session_factory)
    state = _state()
    await h.start_comment(
        _fake_callback(f"recur:occcmt:{slot_id}:{occ_day.isoformat()}"), state, _SP
    )
    assert state.store["flow"] == "occ_comment"
    msg = _fake_message("принести краски")
    await h.apply_occ_comment(msg, state, _SP)
    assert messages.recurring.comment_set in _texts(msg.answer)
    overrides = await _list_overrides(session_factory, slot_id)
    assert len(overrides) == 1
    assert overrides[0].comment == "принести краски"


async def test_start_comment_foreign_blocked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    cb = _fake_callback(f"recur:occcmt:{slot_id}:2026-06-22")
    await h.start_comment(cb, _state(), other)
    cb.message.edit_text.assert_not_awaited()


async def test_apply_occ_comment_foreign_reports_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    other = await _seed_other_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=date(2026, 6, 1)
    )
    h = _recur(messages, session_factory)
    state = _state(
        {
            "flow": "occ_comment",
            "slot_id": slot_id,
            "origin_date": "2026-06-22",
            "card": "",
        }
    )
    msg = _fake_message("x")
    await h.apply_occ_comment(msg, state, other)
    assert messages.recurring.not_found in _texts(msg.answer)
    assert await _list_overrides(session_factory, slot_id) == []


# --- cancel -----------------------------------------------------------------


async def test_cancel_clears_state_and_returns_to_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    today = today_in_tz(datetime.now(UTC), _TZ)
    schedule_id = await _seed_schedule(session_factory, client_id=client_id)
    occ_day = _next_weekday(today, 0)
    slot_id = await _seed_slot(
        session_factory, schedule_id=schedule_id, weekday=0, start_date=occ_day
    )
    h = _recur(messages, session_factory)
    card = f"recur:occ:{slot_id}:{occ_day.isoformat()}"
    state = _state({"flow": "move", "card": card})
    cb = _fake_callback("recur:cancel")
    await h.cancel(cb, state, _SP)
    assert messages.recurring.cancelled in _texts(cb.message.edit_text)
    assert await state.get_data() == {}
    # The cancel re-opens the meeting card it started from.
    cbs = _callbacks(_markup(cb.message.answer))
    assert any(c and c.startswith(f"recur:occmove:{slot_id}:") for c in cbs)


async def test_cancel_without_card_falls_back(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    await _seed_client(session_factory)
    h = _recur(messages, session_factory)
    state = _state({"flow": "rcreate"})
    cb = _fake_callback("recur:cancel")
    await h.cancel(cb, state, _SP)
    assert messages.recurring.cancelled in _texts(cb.message.edit_text)
    assert await state.get_data() == {}
    # Falls back to today's schedule day view.
    assert _markup(cb.message.answer) is not None


# --- helper to enumerate schedules ------------------------------------------


async def _load_schedules(
    factory: async_sessionmaker[AsyncSession],
) -> list[RecurringSchedule]:
    async with factory() as session:
        return await SqlAlchemyRecurringScheduleRepo(
            session
        ).list_active_for_specialist(_SP)
