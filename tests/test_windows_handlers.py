from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.windows import WindowsHandlers, render_windows
from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.domain.recurring import RecurringAppointment
from src.domain.schedule import format_ru_date, next_working_days, today_in_tz
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.recurring_repo import SqlAlchemyRecurringRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import DayWindows, create_appointment
from src.services.invites import create_invite

_SP = 1
_TZ = "Asia/Yekaterinburg"  # default specialist timezone


def _fake_message() -> AsyncMock:
    msg = AsyncMock()
    msg.answer = AsyncMock()
    return msg


def _text(msg: AsyncMock) -> str:
    return msg.answer.await_args.args[0]


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert specialist.id is not None
    return specialist.id


def _handlers(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> WindowsHandlers:
    return WindowsHandlers(messages, factory)


def test_render_windows_groups_days_and_marks_empty():
    m = load_messages(DEFAULT_MESSAGES_PATH).windows
    windows = [
        DayWindows(day=date(2026, 6, 5), free=["09:00", "10:00"]),
        DayWindows(day=date(2026, 6, 8), free=[]),
    ]
    text = render_windows(windows, m)
    assert m.title in text
    assert "5 июня" in text
    assert "09:00, 10:00" in text
    assert m.empty_day in text  # the fully-booked day still appears


async def test_show_no_working_days_branch(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            sp_id, {"working_days": ""}
        )
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show(msg, sp_id)
    assert _text(msg) == messages.windows.no_working_days


async def test_show_renders_free_and_fully_booked_day(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    # Narrow the grid to two slots so a day is easy to fully book.
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            sp_id, {"day_start": "09:00", "day_end": "11:00", "slot_minutes": 60}
        )
    now = datetime.now(UTC)
    today = today_in_tz(now, _TZ)
    # The 5th working day is always far enough ahead to avoid today's past-hiding.
    target = next_working_days(today, {0, 1, 2, 3, 4}, 5)[-1]
    async with session_factory() as session:
        repo = SqlAlchemyAppointmentsRepo(session)
        for hhmm in ("09:00", "10:00"):
            await create_appointment(
                repo,
                specialist_id=sp_id,
                client_id=7,
                day=target,
                hhmm=hhmm,
                comment=None,
                tz=_TZ,
                now=now,
            )
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show(msg, sp_id)
    text = _text(msg)
    assert messages.windows.title in text
    assert messages.windows.empty_day in text  # the fully-booked target day
    assert "09:00" in text  # other working days still have free slots


async def test_show_excludes_series_repeat_slot(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    now = datetime.now(UTC)
    today = today_in_tz(now, _TZ)
    # The next working day with the series' weekday gets a repeat at 14:00.
    target = next_working_days(today, {0, 1, 2, 3, 4}, 5)[1]
    async with session_factory() as session:
        await SqlAlchemyRecurringRepo(session).add(
            RecurringAppointment(
                id=None,
                specialist_id=sp_id,
                client_id=1,
                weekday=target.weekday(),
                time_hhmm="14:00",
                comment=None,
                active=True,
                start_date=target,
                materialized_through=target,
                created_at=now,
                updated_at=now,
            )
        )
    msg = _fake_message()
    await _handlers(messages, session_factory).show(msg, sp_id)
    text = _text(msg)
    # The target day's 14:00 slot is occupied by the repeat → not a free window.
    day_header = messages.windows.day_header.format(date=format_ru_date(target))
    block = text.split(day_header, 1)[1].split("\n\n", 1)[0]
    assert "14:00" not in block
