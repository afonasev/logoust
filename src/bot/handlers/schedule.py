import calendar as _calendar
from datetime import UTC, date, datetime, timedelta
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
from src.bot.messages import BotMessages, RecurringMessages, ScheduleMessages
from src.domain.appointment import Appointment
from src.domain.recurring import RecurringAppointment, RecurringException
from src.domain.schedule import (
    RU_MONTHS_NOMINATIVE,
    RU_WEEKDAYS,
    RU_WEEKDAYS_EVERY,
    RU_WEEKDAYS_SHORT,
    format_ru_date,
    format_ru_short,
    generate_slots,
    parse_hhmm,
    parse_working_days,
    today_in_tz,
    utc_to_wall,
    wall_to_utc,
)
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import (
    AppointmentsPage,
    DayGroup,
    HistoryWeek,
    adjacent_shown_day,
    create_appointment,
    delete_appointment,
    group_by_day,
    history_week_monday,
    list_client_history_page,
    list_specialist_day,
    list_specialist_history_week,
    list_specialist_week,
    reschedule_appointment,
    schedule_landing_day,
    taken_slot_times,
)
from src.services.clients import client_name_map
from src.services.recurring import (
    SeriesContext,
    create_series,
    edit_series,
    load_series_context,
    move_date,
    skip_date,
    stop_series,
)
from src.services.specialists import get_settings

logger = logging.getLogger(__name__)

_HISTORY_PAGE_SIZE = 8
_SLOTS_PER_ROW = 3
_MONTHS_IN_YEAR = 12

_BTN_CANCEL = "Отмена"
_BTN_BACK = "⬅️ Назад"
# Slot occupancy markers: free vs already booked by this specialist at that time.
_SLOT_FREE = "🟢"
_SLOT_TAKEN = "🔴"
# Today's cell in the calendar is highlighted (Telegram can't colour buttons).
_TODAY_MARK = "🟢"
_CB_NOOP = "sched:noop"
_CB_CANCEL = "sched:cancel"
_CB_FEED = "sched:feed"
_CB_SKIP = "sched:skip"


class Schedule(StatesGroup):
    custom_time = State()
    comment = State()


async def _series_context(
    session: AsyncSession, specialist_id: int, tz: str
) -> SeriesContext:
    return await load_series_context(
        SqlAlchemyRecurringRepo(session),
        SqlAlchemyRecurringExceptionsRepo(session),
        specialist_id=specialist_id,
        now=datetime.now(UTC),
        tz=tz,
    )


# --- pure builders ------------------------------------------------------------


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * _MONTHS_IN_YEAR + (month - 1)) + delta
    return index // _MONTHS_IN_YEAR, index % _MONTHS_IN_YEAR + 1


def _noop_button(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=_CB_NOOP)


def _calendar_header(
    year: int, month: int, today: date, prefix: str
) -> list[InlineKeyboardButton]:
    # Allow navigating back only to the current month; earlier months are useless
    # because every day in them is past and therefore inactive.
    if (year, month) > (today.year, today.month):
        py, pm = _shift_month(year, month, -1)
        prev_btn = InlineKeyboardButton(
            text="◀", callback_data=f"{prefix}:cal:{py}:{pm}"
        )
    else:
        prev_btn = _noop_button(" ")
    ny, nm = _shift_month(year, month, 1)
    return [
        prev_btn,
        _noop_button(f"{RU_MONTHS_NOMINATIVE[month]} {year}"),
        InlineKeyboardButton(text="▶", callback_data=f"{prefix}:cal:{ny}:{nm}"),
    ]


def _day_button(
    year: int, month: int, day: int, today: date, prefix: str
) -> InlineKeyboardButton:
    if day == 0:
        return _noop_button("·")
    current = date(year, month, day)
    if current < today:
        # Past day: shown but inert, so it cannot be selected (spec).
        return _noop_button(str(day))
    text = f"{_TODAY_MARK}{day}" if current == today else str(day)
    return InlineKeyboardButton(
        text=text, callback_data=f"{prefix}:day:{year}:{month}:{day}"
    )


def build_calendar(
    year: int,
    month: int,
    today: date,
    *,
    prefix: str = "sched",
    cancel_cb: str = _CB_CANCEL,
) -> InlineKeyboardMarkup:
    # `prefix` namespaces the day/nav callbacks so the same calendar serves both
    # the appointment flow (`sched:*`) and the recurring move flow (`recur:*`).
    rows = [_calendar_header(year, month, today, prefix)]
    rows.append([_noop_button(name) for name in RU_WEEKDAYS_SHORT])
    weeks = _calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    rows.extend(
        [_day_button(year, month, day, today, prefix) for day in week] for week in weeks
    )
    rows.append([InlineKeyboardButton(text=_BTN_CANCEL, callback_data=cancel_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_slots_keyboard(
    slots: list[str], taken: set[str], m: ScheduleMessages
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in slots:
        marker = _SLOT_TAKEN if slot in taken else _SLOT_FREE
        row.append(
            InlineKeyboardButton(
                text=f"{marker} {slot}",
                callback_data=f"sched:slot:{slot.replace(':', '')}",
            )
        )
        if len(row) == _SLOTS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.extend(
        (
            [InlineKeyboardButton(text=m.btn_other_time, callback_data="sched:other")],
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)],
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data=_CB_SKIP),
                InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL),
            ]
        ]
    )


def _regular_keyboard(m: ScheduleMessages) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_regular_yes, callback_data="sched:reg:1"
                ),
                InlineKeyboardButton(
                    text=m.btn_regular_no, callback_data="sched:reg:0"
                ),
            ],
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)],
        ]
    )


# --- formatting ---------------------------------------------------------------


def _comment_part(comment: str | None, m: ScheduleMessages) -> str:
    return m.comment_suffix.format(comment=comment) if comment else ""


def render_card(appt: Appointment, child: str, tz: str, m: ScheduleMessages) -> str:
    wall = utc_to_wall(appt.starts_at, tz)
    return m.card.format(
        child=child,
        date=format_ru_date(wall.date()),
        time=f"{wall:%H:%M}",
        comment=appt.comment or m.dash,
    )


def _card_keyboard(
    appt: Appointment, m: ScheduleMessages, back: str
) -> InlineKeyboardMarkup:
    # `back` is the callback_data of wherever the card was opened from.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_reschedule, callback_data=f"sched:resch:{appt.id}"
                ),
                InlineKeyboardButton(
                    text=m.btn_delete, callback_data=f"sched:del:{appt.id}~{back}"
                ),
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        ]
    )


def _grouped_line(
    appt: Appointment,
    names: dict[int, str],
    tz: str,
    m: ScheduleMessages,
    rm: RecurringMessages,
) -> str:
    # Only plain future occurrences carry the 🔁 line (moved/one-off do not).
    template = rm.line if appt.recurring_mark else m.line
    return template.format(
        child=names.get(appt.client_id, m.dash),
        time=f"{utc_to_wall(appt.starts_at, tz):%H:%M}",
        comment=_comment_part(appt.comment, m),
    )


def _render_grouped(  # noqa: PLR0913
    title: str,
    groups: list[DayGroup],
    names: dict[int, str],
    tz: str,
    *,
    m: ScheduleMessages,
    rm: RecurringMessages,
) -> str:
    blocks: list[str] = []
    for group in groups:
        header = m.day_header.format(date=format_ru_date(group.day))
        lines = [_grouped_line(appt, names, tz, m, rm) for appt in group.appointments]
        blocks.append("\n".join([header, *lines]))
    return "\n\n".join([title, *blocks])


def _full_line(appt: Appointment, child: str, tz: str, m: ScheduleMessages) -> str:
    wall = utc_to_wall(appt.starts_at, tz)
    return m.line_full.format(
        child=child,
        date=format_ru_date(wall.date()),
        time=f"{wall:%H:%M}",
        comment=_comment_part(appt.comment, m),
    )


def _card_button(
    appt: Appointment, label: str, back: str
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=label, callback_data=f"sched:card:{appt.id}~{back}")
    ]


def _appt_row(  # noqa: PLR0913
    appt: Appointment,
    names: dict[int, str],
    tz: str,
    *,
    m: ScheduleMessages,
    rm: RecurringMessages,
    back: str,
) -> list[InlineKeyboardButton]:
    # A virtual occurrence (id is None) routes to the series card by
    # series_id/origin_date; a real row routes to its appointment card.
    child = names.get(appt.client_id, m.dash)
    time = f"{utc_to_wall(appt.starts_at, tz):%H:%M}"
    # Only a plain occurrence is marked 🔁; a moved one looks like a one-off.
    mark = f"{rm.mark} " if appt.recurring_mark else ""
    label = f"{mark}{time} · {child}{_comment_part(appt.comment, m)}"
    if appt.id is None:
        assert appt.series_id is not None  # noqa: S101 — virtual rows carry a series
        assert appt.origin_date is not None  # noqa: S101
        # Carry the day view as the origin so the series card returns here.
        callback = f"recur:card:{appt.series_id}:{appt.origin_date.isoformat()}~{back}"
        return [InlineKeyboardButton(text=label, callback_data=callback)]
    return _card_button(appt, label, back)


def _render_day(day: date, appts: list[Appointment], m: ScheduleMessages) -> str:
    # Day view = just the header; appointments live in the keyboard as buttons
    # (no duplicate text list).
    header = m.day_title.format(date=format_ru_date(day))
    return header if appts else f"{header}\n\n{m.day_empty}"


def _day_keyboard(  # noqa: PLR0913
    day: date,
    appts: list[Appointment],
    names: dict[int, str],
    tz: str,
    *,
    m: ScheduleMessages,
    rm: RecurringMessages,
    prev_day: date | None,
    next_day: date | None,
) -> InlineKeyboardMarkup:
    back = f"sched:day_view:{day.isoformat()}"
    rows = [_appt_row(appt, names, tz, m=m, rm=rm, back=back) for appt in appts]
    # ◀/▶ skip empty non-working days: their targets are the nearest shown day in
    # each direction (None ⇒ nothing to show that way, so omit the arrow).
    nav = []
    if prev_day is not None:
        nav.append(
            InlineKeyboardButton(
                text="◀", callback_data=f"sched:day_view:{prev_day.isoformat()}"
            )
        )
    if next_day is not None:
        nav.append(
            InlineKeyboardButton(
                text="▶", callback_data=f"sched:day_view:{next_day.isoformat()}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text=m.btn_week, callback_data="sched:week"),
            InlineKeyboardButton(text=m.btn_history, callback_data="sched:hist:0"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _week_keyboard(m: ScheduleMessages) -> InlineKeyboardMarkup:
    # Week is a read-only overview (grouped text); only navigation buttons here.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=m.btn_today, callback_data=_CB_FEED),
                InlineKeyboardButton(text=m.btn_history, callback_data="sched:hist:0"),
            ]
        ]
    )


def _nav_row(
    *, has_prev: bool, has_next: bool, page: int, prefix: str
) -> list[InlineKeyboardButton]:
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"{prefix}{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"{prefix}{page + 1}"))
    return nav


def _history_keyboard(hw: HistoryWeek, m: ScheduleMessages) -> InlineKeyboardMarkup:
    # History is read-only text, paged by week: ◀ older week, ▶ newer week.
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if hw.has_older:
        nav.append(
            InlineKeyboardButton(text="◀", callback_data=f"sched:hist:{hw.week + 1}")
        )
    if hw.has_newer:
        nav.append(
            InlineKeyboardButton(text="▶", callback_data=f"sched:hist:{hw.week - 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=m.btn_today, callback_data=_CB_FEED)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _client_back_row(client_id: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"clients:card:{client_id}")
    ]


def _client_history_keyboard(
    client_id: int, page_obj: AppointmentsPage
) -> InlineKeyboardMarkup:
    # Read-only text: only page navigation and a way back to the client card.
    rows: list[list[InlineKeyboardButton]] = []
    nav = _nav_row(
        has_prev=page_obj.has_prev,
        has_next=page_obj.has_next,
        page=page_obj.page,
        prefix=f"sched:chist:{client_id}:",
    )
    if nav:
        rows.append(nav)
    rows.append(_client_back_row(client_id))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _delete_confirm_keyboard(
    appointment_id: int, back: str, m: ScheduleMessages
) -> InlineKeyboardMarkup:
    # `back` is threaded through so the post-delete screen returns to the card's
    # origin (day view / client card), not the schedule feed.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_confirm_delete,
                    callback_data=f"sched:delyes:{appointment_id}~{back}",
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL,
                    callback_data=f"sched:card:{appointment_id}~{back}",
                ),
            ]
        ]
    )


def _deleted_keyboard(
    m: ScheduleMessages, back: str | None = None
) -> InlineKeyboardMarkup:
    # With a contextual `back`, go back to where the card was opened from;
    # otherwise (recurring stop/skip/cancel) fall back to today's feed.
    if back:
        button = InlineKeyboardButton(text=_BTN_BACK, callback_data=back)
    else:
        button = InlineKeyboardButton(text=m.btn_today, callback_data=_CB_FEED)
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)]
        ]
    )


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _last_int(callback_data: str | None) -> int:
    return int((callback_data or "").rsplit(":", 1)[1])


# --- handlers -----------------------------------------------------------------


class ScheduleHandlers:  # noqa: PLR0904 — handler aggregator for the schedule router
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.schedule
        self._rm = messages.recurring
        self._session_factory = session_factory

    async def _load_settings(self, specialist_id: int) -> Specialist:
        async with self._session_factory() as session:
            specialist = await get_settings(
                SqlAlchemySpecialistsRepo(session), specialist_id
            )
        assert specialist is not None  # noqa: S101 — middleware guarantees existence
        return specialist

    async def _child_name(self, specialist_id: int, client_id: int) -> str:
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        return client.child_name if client is not None else self._m.dash

    # --- picker entry & navigation -------------------------------------------

    async def start_create(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        await state.update_data(flow="create", client_id=_last_int(callback.data))
        await self._show_calendar(callback, specialist_id)

    async def start_reschedule(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        await state.update_data(
            flow="reschedule", appointment_id=_last_int(callback.data)
        )
        await self._show_calendar(callback, specialist_id)

    async def _show_calendar(self, callback: CallbackQuery, specialist_id: int) -> None:
        specialist = await self._load_settings(specialist_id)
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        await _callback_message(callback).edit_text(
            self._m.pick_date,
            reply_markup=build_calendar(today.year, today.month, today),
        )
        await callback.answer()

    async def navigate(self, callback: CallbackQuery, specialist_id: int) -> None:
        _, _, year, month = (callback.data or "").split(":")
        specialist = await self._load_settings(specialist_id)
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        await _callback_message(callback).edit_reply_markup(
            reply_markup=build_calendar(int(year), int(month), today)
        )
        await callback.answer()

    @staticmethod
    async def noop(callback: CallbackQuery) -> None:
        await callback.answer()

    async def pick_day(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        _, _, year, month, day = (callback.data or "").split(":")
        chosen = date(int(year), int(month), int(day))
        data = await state.get_data()
        await state.update_data(day=chosen.isoformat())
        specialist = await self._load_settings(specialist_id)
        slots = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        async with self._session_factory() as session:
            series = await _series_context(session, specialist_id, specialist.timezone)
            taken = await taken_slot_times(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                day=chosen,
                tz=specialist.timezone,
                exclude_id=data.get("appointment_id"),
                series=series,
            )
        await _callback_message(callback).edit_text(
            self._m.pick_time, reply_markup=build_slots_keyboard(slots, taken, self._m)
        )
        await callback.answer()

    # --- time selection ------------------------------------------------------

    async def pick_slot(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        raw = (callback.data or "").rsplit(":", 1)[1]
        hhmm = f"{raw[:2]}:{raw[2:]}"
        await self._proceed(_callback_message(callback), state, specialist_id, hhmm)
        await callback.answer()

    async def ask_custom_time(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(Schedule.custom_time)
        await _callback_message(callback).edit_text(
            self._m.ask_custom_time, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def apply_custom_time(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        hhmm = parse_hhmm(message.text or "")
        if hhmm is None:
            await message.answer(self._m.bad_time, reply_markup=_cancel_keyboard())
            return
        await self._proceed(message, state, specialist_id, hhmm)

    async def _proceed(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        if data.get("flow") == "create":
            # Ask whether this should repeat weekly before the comment step.
            await state.update_data(hhmm=hhmm)
            await target.answer(
                self._m.ask_regular, reply_markup=_regular_keyboard(self._m)
            )
        else:
            await self._do_reschedule(target, state, specialist_id, hhmm)

    async def choose_regular(self, callback: CallbackQuery, state: FSMContext) -> None:
        regular = (callback.data or "").rsplit(":", 1)[1] == "1"
        await state.update_data(regular=regular)
        await state.set_state(Schedule.comment)
        await _callback_message(callback).edit_text(
            self._m.ask_comment, reply_markup=_skip_keyboard()
        )
        await callback.answer()

    # --- create finish -------------------------------------------------------

    async def apply_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        comment = (message.text or "").strip() or None
        await self._finish_create(message, state, specialist_id, comment)

    async def skip_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await self._finish_create(
            _callback_message(callback), state, specialist_id, None
        )
        await callback.answer()

    async def _finish_create(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        comment: str | None,
    ) -> None:
        data = await state.get_data()
        if data.get("regular"):
            await self._finish_regular(target, state, specialist_id, comment)
        else:
            await self._finish_one_off(target, state, specialist_id, comment)

    async def _finish_one_off(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        comment: str | None,
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            appt = await create_appointment(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                client_id=data["client_id"],
                day=date.fromisoformat(data["day"]),
                hhmm=data["hhmm"],
                comment=comment,
                tz=specialist.timezone,
                now=datetime.now(UTC),
            )
        child = await self._child_name(specialist_id, data["client_id"])
        await state.clear()
        await target.answer(self._m.created)
        # Back from a freshly created appointment returns to the client card.
        await self._send_card(
            target,
            appt,
            child,
            specialist.timezone,
            f"clients:card:{data['client_id']}",
        )

    async def _finish_regular(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        comment: str | None,
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        client_id = data["client_id"]
        day = date.fromisoformat(data["day"])
        async with self._session_factory() as session:
            series = await create_series(
                SqlAlchemyRecurringRepo(session),
                specialist_id=specialist_id,
                client_id=client_id,
                weekday=day.weekday(),
                time_hhmm=data["hhmm"],
                comment=comment,
                tz=specialist.timezone,
                now=datetime.now(UTC),
                start_date=day,  # the picked date is the first occurrence
            )
        assert series.id is not None  # noqa: S101 — saved series always have an id
        child = await self._child_name(specialist_id, client_id)
        await state.clear()
        await target.answer(self._rm.created)
        await target.answer(
            render_series_card(
                series,
                child,
                series.start_date,
                None,
                specialist.timezone,
                self._rm,
                self._m.dash,
            ),
            reply_markup=_series_card_keyboard(
                series.id, series.start_date, self._rm, f"clients:card:{client_id}"
            ),
        )

    async def _do_reschedule(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            moved = await reschedule_appointment(
                SqlAlchemyAppointmentsRepo(session),
                appointment_id=data["appointment_id"],
                specialist_id=specialist_id,
                day=date.fromisoformat(data["day"]),
                hhmm=hhmm,
                tz=specialist.timezone,
                now=datetime.now(UTC),
            )
        await state.clear()
        if moved is None:
            await target.answer(self._m.not_found)
            return
        child = await self._child_name(specialist_id, moved.client_id)
        await target.answer(self._m.rescheduled)
        # Back from a rescheduled appointment returns to its (new) day.
        await self._send_card(
            target, moved, child, specialist.timezone, f"sched:day_view:{data['day']}"
        )

    async def _send_card(
        self, target: Message, appt: Appointment, child: str, tz: str, back: str
    ) -> None:
        await target.answer(
            render_card(appt, child, tz, self._m),
            reply_markup=_card_keyboard(appt, self._m, back),
        )

    # --- card, delete --------------------------------------------------------

    async def show_card(self, callback: CallbackQuery, specialist_id: int) -> None:
        # "sched:card:<id>[~<back-callback>]" — back returns to the origin.
        head, _, back = (callback.data or "").partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            appt = await SqlAlchemyAppointmentsRepo(session).get_for_specialist(
                appt_id, specialist_id
            )
        if appt is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        tz = specialist.timezone
        if not back:  # default: the appointment's own day
            back = (
                f"sched:day_view:{utc_to_wall(appt.starts_at, tz).date().isoformat()}"
            )
        child = await self._child_name(specialist_id, appt.client_id)
        await _callback_message(callback).edit_text(
            render_card(appt, child, tz, self._m),
            reply_markup=_card_keyboard(appt, self._m, back),
        )
        await callback.answer()

    async def confirm_delete(self, callback: CallbackQuery) -> None:
        # "sched:del:<id>[~<back-callback>]" — preserve the origin for after delete.
        head, _, back = (callback.data or "").partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        await _callback_message(callback).edit_text(
            self._m.confirm_delete,
            reply_markup=_delete_confirm_keyboard(appt_id, back, self._m),
        )
        await callback.answer()

    async def do_delete(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, _, back = (callback.data or "").partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        async with self._session_factory() as session:
            await delete_appointment(
                SqlAlchemyAppointmentsRepo(session),
                appointment_id=appt_id,
                specialist_id=specialist_id,
            )
        await _callback_message(callback).edit_text(
            self._m.deleted, reply_markup=_deleted_keyboard(self._m, back or None)
        )
        await callback.answer()

    async def cancel(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        await self._edit_day(callback, specialist_id, None)

    # --- specialist day / week / history -------------------------------------

    async def show_feed(self, message: Message, specialist_id: int) -> None:
        text, keyboard = await self._day_view(specialist_id, None)
        await message.answer(text, reply_markup=keyboard)

    async def open_day(self, callback: CallbackQuery, specialist_id: int) -> None:
        # Serves both the "today" entry (sched:feed) and day navigation
        # (sched:day_view:<iso>).
        data = callback.data or ""
        day = None if data == _CB_FEED else date.fromisoformat(data.rsplit(":", 1)[1])
        await self._edit_day(callback, specialist_id, day)

    async def _edit_day(
        self, callback: CallbackQuery, specialist_id: int, day: date | None
    ) -> None:
        text, keyboard = await self._day_view(specialist_id, day)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def _day_view(
        self, specialist_id: int, day: date | None
    ) -> tuple[str, InlineKeyboardMarkup]:
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        working = set(parse_working_days(specialist.working_days))
        async with self._session_factory() as session:
            repo = SqlAlchemyAppointmentsRepo(session)
            series = await _series_context(session, specialist_id, tz)
            if day is None:  # entry point lands on the nearest shown day from today
                day = await schedule_landing_day(
                    repo,
                    specialist_id=specialist_id,
                    working_days=working,
                    tz=tz,
                    today=today_in_tz(datetime.now(UTC), tz),
                    series=series,
                )
            appts = await list_specialist_day(
                repo, specialist_id=specialist_id, day=day, tz=tz, series=series
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
            prev_day = await adjacent_shown_day(
                repo,
                specialist_id=specialist_id,
                working_days=working,
                tz=tz,
                day=day,
                forward=False,
            )
            next_day = await adjacent_shown_day(
                repo,
                specialist_id=specialist_id,
                working_days=working,
                tz=tz,
                day=day,
                forward=True,
            )
        return _render_day(day, appts, self._m), _day_keyboard(
            day,
            appts,
            names,
            tz,
            m=self._m,
            rm=self._rm,
            prev_day=prev_day,
            next_day=next_day,
        )

    async def show_week(self, callback: CallbackQuery, specialist_id: int) -> None:
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        async with self._session_factory() as session:
            series = await _series_context(session, specialist_id, tz)
            groups = await list_specialist_week(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                tz=tz,
                now=datetime.now(UTC),
                series=series,
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        text = (
            self._m.week_empty
            if not groups
            else _render_grouped(
                self._m.week_title, groups, names, tz, m=self._m, rm=self._rm
            )
        )
        await _callback_message(callback).edit_text(
            text, reply_markup=_week_keyboard(self._m)
        )
        await callback.answer()

    async def show_history(self, callback: CallbackQuery, specialist_id: int) -> None:
        week = _last_int(callback.data)
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            hw = await list_specialist_history_week(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                tz=tz,
                now=now,
                week=week,
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        await _callback_message(callback).edit_text(
            self._history_text(hw, names, tz, now),
            reply_markup=_history_keyboard(hw, self._m),
        )
        await callback.answer()

    def _history_text(
        self, hw: HistoryWeek, names: dict[int, str], tz: str, now: datetime
    ) -> str:
        # No history at all yet (newest week empty and nothing older).
        if not hw.appointments and not hw.has_older and hw.week == 0:
            return self._m.history_empty
        monday = history_week_monday(today_in_tz(now, tz), hw.week)
        title = self._m.history_title.format(
            start=format_ru_short(monday),
            end=format_ru_short(monday + timedelta(days=6)),
        )
        if not hw.appointments:
            return f"{title}\n\n{self._m.history_empty}"
        groups = group_by_day(hw.appointments, tz)
        return _render_grouped(title, groups, names, tz, m=self._m, rm=self._rm)

    # --- client history -------------------------------------------------------

    async def show_client_history(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        _, _, raw_client, raw_page = (callback.data or "").split(":")
        client_id, page = int(raw_client), int(raw_page)
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        async with self._session_factory() as session:
            page_obj = await list_client_history_page(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                client_id=client_id,
                tz=tz,
                now=datetime.now(UTC),
                page=page,
                page_size=_HISTORY_PAGE_SIZE,
            )
        child = await self._child_name(specialist_id, client_id)
        if page == 0 and not page_obj.appointments:
            text = self._m.client_history_empty
        else:
            lines = [
                _full_line(appt, child, tz, self._m) for appt in page_obj.appointments
            ]
            text = "\n\n".join(
                [
                    self._m.client_history_title.format(child=child, page=page + 1),
                    *lines,
                ]
            )
        keyboard = _client_history_keyboard(client_id, page_obj)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()


_WEEKDAYS_PER_ROW = 4


class Recurring(StatesGroup):
    custom_time = State()
    comment = State()


def _weekday_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=name, callback_data=f"recur:wd:{index}")
        for index, name in enumerate(RU_WEEKDAYS_SHORT)
    ]
    rows = [
        buttons[i : i + _WEEKDAYS_PER_ROW]
        for i in range(0, len(buttons), _WEEKDAYS_PER_ROW)
    ]
    rows.append([InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recur_slots_keyboard(
    slots: list[str], taken: set[str], m: ScheduleMessages
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{_SLOT_TAKEN if slot in taken else _SLOT_FREE} {slot}",
            callback_data=f"recur:slot:{slot.replace(':', '')}",
        )
        for slot in slots
    ]
    rows = [
        buttons[i : i + _SLOTS_PER_ROW] for i in range(0, len(buttons), _SLOTS_PER_ROW)
    ]
    rows.extend(
        (
            [InlineKeyboardButton(text=m.btn_other_time, callback_data="recur:other")],
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")],
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _recur_skip_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data="recur:skipc"),
                InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel"),
            ]
        ]
    )


def _occurrence_display(
    series: RecurringAppointment,
    origin_date: date,
    exception: "RecurringException | None",
    tz: str,
) -> tuple[date, str]:
    """Effective (date, wall `HH:MM`) of the occurrence — moved date wins over rule."""
    if exception is not None and exception.new_starts_at is not None:
        wall = utc_to_wall(exception.new_starts_at, tz)
        return wall.date(), f"{wall:%H:%M}"
    return origin_date, series.time_hhmm


def render_series_card(  # noqa: PLR0913, PLR0917
    series: RecurringAppointment,
    child: str,
    origin_date: date,
    exception: "RecurringException | None",
    tz: str,
    rm: RecurringMessages,
    dash: str,
) -> str:
    # Show the base rule ("Каждый вторник в 19:00") plus the next meeting's actual
    # date/weekday/time, which differs from the rule when that date was moved.
    eff_date, eff_time = _occurrence_display(series, origin_date, exception, tz)
    return rm.card.format(
        child=child,
        rule=f"{RU_WEEKDAYS_EVERY[series.weekday]} в {series.time_hhmm}",
        date=format_ru_short(eff_date),
        weekday=RU_WEEKDAYS[eff_date.weekday()],
        time=eff_time,
        comment=series.comment or dash,
    )


def _with_back(head: str, back: str) -> str:
    """Append `~back` to a callback only when `back` is non-empty (no dangling `~`)."""
    return f"{head}~{back}" if back else head


def _series_card_keyboard(
    series_id: int, origin_date: date, rm: RecurringMessages, back: str
) -> InlineKeyboardMarkup:
    # `back` is the callback of wherever the series card was opened from (day view
    # or client card) and is threaded through every action that loops back here.
    card = _with_back(f"{series_id}:{origin_date.isoformat()}", back)
    # Mirror the one-off appointment card: top row acts on this date (move / cancel),
    # second row acts on the whole series (configure / cancel all), then back.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_move_date, callback_data=f"recur:move:{card}"
                ),
                InlineKeyboardButton(
                    text=rm.btn_skip_date, callback_data=f"recur:skipask:{card}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rm.btn_edit,
                    callback_data=_with_back(f"recur:edit:{card}", ""),
                ),
                InlineKeyboardButton(
                    text=rm.btn_stop,
                    callback_data=_with_back(f"recur:stopask:{series_id}", back),
                ),
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        ]
    )


def _stop_confirm_keyboard(
    series_id: int, origin_date: date, back: str, rm: RecurringMessages
) -> InlineKeyboardMarkup:
    card = _with_back(f"{series_id}:{origin_date.isoformat()}", back)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_confirm_stop,
                    callback_data=_with_back(f"recur:stop:{series_id}", back),
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL, callback_data=f"recur:card:{card}"
                ),
            ]
        ]
    )


def _skip_confirm_keyboard(
    series_id: int, origin_date: date, back: str, rm: RecurringMessages
) -> InlineKeyboardMarkup:
    card = _with_back(f"{series_id}:{origin_date.isoformat()}", back)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_confirm_skip, callback_data=f"recur:skip:{card}"
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL, callback_data=f"recur:card:{card}"
                ),
            ]
        ]
    )


def _split_back(callback_data: str | None) -> tuple[str, str]:
    """Split `head~back` callback data into `(head, back)`; back is `''` if absent."""
    head, _, back = (callback_data or "").partition("~")
    return head, back


def _parse_series_date(callback_data: str | None) -> tuple[int, date]:
    # "recur:<action>:<series_id>:<origin_date>" — date is a plain ISO day (no colon).
    # Any trailing "~<back>" must be stripped by the caller first (see _split_back).
    _, _, raw_id, raw_date = (callback_data or "").split(":", 3)
    return int(raw_id), date.fromisoformat(raw_date)


class RecurringHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.recurring
        self._sm = messages.schedule
        self._session_factory = session_factory

    async def _load_settings(self, specialist_id: int) -> Specialist:
        async with self._session_factory() as session:
            specialist = await get_settings(
                SqlAlchemySpecialistsRepo(session), specialist_id
            )
        assert specialist is not None  # noqa: S101 — middleware guarantees existence
        return specialist

    async def _child_name(self, specialist_id: int, client_id: int) -> str:
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        return client.child_name if client is not None else self._sm.dash

    async def _load_series(
        self, series_id: int, specialist_id: int
    ) -> RecurringAppointment | None:
        async with self._session_factory() as session:
            return await SqlAlchemyRecurringRepo(session).get_for_specialist(
                series_id, specialist_id
            )

    # --- edit entry ----------------------------------------------------------

    async def start_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        head, back = _split_back(callback.data)
        series_id, origin_date = _parse_series_date(head)
        await state.update_data(
            flow="edit",
            series_id=series_id,
            back=back,
            # Where Cancel returns: the series card this edit was started from.
            card=_with_back(f"recur:card:{series_id}:{origin_date.isoformat()}", back),
        )
        await _callback_message(callback).edit_text(
            self._m.pick_weekday, reply_markup=_weekday_keyboard()
        )
        await callback.answer()

    async def pick_weekday(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.update_data(weekday=_last_int(callback.data))
        specialist = await self._load_settings(specialist_id)
        slots = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        await _callback_message(callback).edit_text(
            self._m.pick_time,
            reply_markup=build_recur_slots_keyboard(slots, set(), self._sm),
        )
        await callback.answer()

    # --- move entry: pick new date, then new time ----------------------------

    async def start_move(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        series_id, origin_date = _parse_series_date(head)
        series = await self._load_series(series_id, specialist_id)
        if series is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(
            flow="move",
            series_id=series_id,
            origin_date=origin_date.isoformat(),
            back=back,
            # Where Cancel returns: the series card this move was started from.
            card=_with_back(f"recur:card:{series_id}:{origin_date.isoformat()}", back),
        )
        specialist = await self._load_settings(specialist_id)
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        await _callback_message(callback).edit_text(
            self._m.pick_move_date,
            reply_markup=build_calendar(
                today.year, today.month, today, prefix="recur", cancel_cb="recur:cancel"
            ),
        )
        await callback.answer()

    async def navigate_move(self, callback: CallbackQuery, specialist_id: int) -> None:
        _, _, year, month = (callback.data or "").split(":")
        specialist = await self._load_settings(specialist_id)
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        await _callback_message(callback).edit_reply_markup(
            reply_markup=build_calendar(
                int(year), int(month), today, prefix="recur", cancel_cb="recur:cancel"
            )
        )
        await callback.answer()

    async def pick_move_day(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        _, _, year, month, day = (callback.data or "").split(":")
        chosen = date(int(year), int(month), int(day))
        await state.update_data(new_day=chosen.isoformat())
        specialist = await self._load_settings(specialist_id)
        slots = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        async with self._session_factory() as session:
            ctx = await _series_context(session, specialist_id, specialist.timezone)
            taken = await taken_slot_times(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                day=chosen,
                tz=specialist.timezone,
                series=ctx,
            )
        await _callback_message(callback).edit_text(
            self._m.pick_time,
            reply_markup=build_recur_slots_keyboard(slots, taken, self._sm),
        )
        await callback.answer()

    # --- time selection ------------------------------------------------------

    async def pick_slot(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        raw = (callback.data or "").rsplit(":", 1)[1]
        hhmm = f"{raw[:2]}:{raw[2:]}"
        await self._proceed_time(
            _callback_message(callback), state, specialist_id, hhmm
        )
        await callback.answer()

    async def ask_custom_time(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(Recurring.custom_time)
        await _callback_message(callback).edit_text(
            self._m.ask_custom_time, reply_markup=_recur_cancel_keyboard()
        )
        await callback.answer()

    async def apply_custom_time(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        hhmm = parse_hhmm(message.text or "")
        if hhmm is None:
            await message.answer(
                self._m.bad_time, reply_markup=_recur_cancel_keyboard()
            )
            return
        await self._proceed_time(message, state, specialist_id, hhmm)

    async def _proceed_time(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        if data.get("flow") == "move":
            await self._do_move(target, state, specialist_id, hhmm)
            return
        await state.update_data(hhmm=hhmm)
        await state.set_state(Recurring.comment)
        await target.answer(
            self._m.ask_comment, reply_markup=_recur_skip_comment_keyboard()
        )

    # --- edit finish ---------------------------------------------------------

    async def apply_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        comment = (message.text or "").strip() or None
        await self._finish(message, state, specialist_id, comment)

    async def skip_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await self._finish(_callback_message(callback), state, specialist_id, None)
        await callback.answer()

    async def _finish(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        comment: str | None,
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            series = await edit_series(
                SqlAlchemyRecurringRepo(session),
                series_id=data["series_id"],
                specialist_id=specialist_id,
                weekday=data["weekday"],
                time_hhmm=data["hhmm"],
                comment=comment,
                now=datetime.now(UTC),
                tz=specialist.timezone,
            )
        back = data["back"]  # set by start_edit (the series card's origin)
        await state.clear()
        if series is None:  # edit of a series the specialist does not own
            await target.answer(self._m.not_found)
            return
        await target.answer(self._m.created)
        await self._send_series_card(target, specialist_id, series, back)

    async def _do_move(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        origin_date = date.fromisoformat(data["origin_date"])  # the date being moved
        new_day = date.fromisoformat(data["new_day"])  # where it goes
        specialist = await self._load_settings(specialist_id)
        new_starts_at = wall_to_utc(new_day, hhmm, specialist.timezone)
        async with self._session_factory() as session:
            moved = await move_date(
                SqlAlchemyRecurringRepo(session),
                SqlAlchemyRecurringExceptionsRepo(session),
                series_id=data["series_id"],
                specialist_id=specialist_id,
                original_date=origin_date,
                new_starts_at=new_starts_at,
                now=datetime.now(UTC),
            )
        card = data.get("card") or ""
        await state.clear()
        if moved is None:
            await target.answer(self._m.not_found)
            return
        await target.answer(
            self._m.moved, reply_markup=_deleted_keyboard(self._sm, card or None)
        )

    # --- series card ---------------------------------------------------------

    async def _load_exception(
        self, series_id: int, origin_date: date
    ) -> RecurringException | None:
        async with self._session_factory() as session:
            exceptions = await SqlAlchemyRecurringExceptionsRepo(
                session
            ).list_for_series(series_id)
        return next((e for e in exceptions if e.original_date == origin_date), None)

    async def show_card(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        series_id, origin_date = _parse_series_date(head)
        series = await self._load_series(series_id, specialist_id)
        if series is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        specialist = await self._load_settings(specialist_id)
        exception = await self._load_exception(series_id, origin_date)
        back = back or f"sched:day_view:{origin_date.isoformat()}"
        child = await self._child_name(specialist_id, series.client_id)
        await _callback_message(callback).edit_text(
            render_series_card(
                series,
                child,
                origin_date,
                exception,
                specialist.timezone,
                self._m,
                self._sm.dash,
            ),
            reply_markup=_series_card_keyboard(series_id, origin_date, self._m, back),
        )
        await callback.answer()

    async def _send_series_card(
        self,
        target: Message,
        specialist_id: int,
        series: RecurringAppointment,
        back: str,
    ) -> None:
        assert series.id is not None  # noqa: S101 — saved series always have an id
        specialist = await self._load_settings(specialist_id)
        child = await self._child_name(specialist_id, series.client_id)
        # A freshly created/edited series has no exception on its first occurrence.
        await target.answer(
            render_series_card(
                series,
                child,
                series.start_date,
                None,
                specialist.timezone,
                self._m,
                self._sm.dash,
            ),
            reply_markup=_series_card_keyboard(
                series.id, series.start_date, self._m, back
            ),
        )

    # --- stop ----------------------------------------------------------------

    async def confirm_stop(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        series_id = _last_int(head)
        series = await self._load_series(series_id, specialist_id)
        if series is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.confirm_stop,
            reply_markup=_stop_confirm_keyboard(
                series_id, series.start_date, back, self._m
            ),
        )
        await callback.answer()

    async def do_stop(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        series_id = _last_int(head)
        async with self._session_factory() as session:
            stopped = await stop_series(
                SqlAlchemyRecurringRepo(session),
                series_id=series_id,
                specialist_id=specialist_id,
                now=datetime.now(UTC),
            )
        if stopped is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.stopped, reply_markup=_deleted_keyboard(self._sm, back or None)
        )
        await callback.answer()

    # --- skip ----------------------------------------------------------------

    async def confirm_skip(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        series_id, origin_date = _parse_series_date(head)
        series = await self._load_series(series_id, specialist_id)
        if series is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.skip_confirm.format(date=format_ru_date(origin_date)),
            reply_markup=_skip_confirm_keyboard(series_id, origin_date, back, self._m),
        )
        await callback.answer()

    async def do_skip(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        series_id, origin_date = _parse_series_date(head)
        async with self._session_factory() as session:
            skipped = await skip_date(
                SqlAlchemyRecurringRepo(session),
                SqlAlchemyRecurringExceptionsRepo(session),
                series_id=series_id,
                specialist_id=specialist_id,
                original_date=origin_date,
                now=datetime.now(UTC),
            )
        if skipped is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.skipped, reply_markup=_deleted_keyboard(self._sm, back or None)
        )
        await callback.answer()

    async def cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        card = data.get("card") or ""  # the series card this flow started from
        await state.clear()
        await _callback_message(callback).edit_text(
            self._m.cancelled, reply_markup=_deleted_keyboard(self._sm, card or None)
        )
        await callback.answer()


def _recur_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")]
        ]
    )


def _register_recurring(
    router: Router,
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    r = RecurringHandlers(messages, session_factory)
    router.message.register(r.apply_custom_time, Recurring.custom_time)
    router.message.register(r.apply_comment, Recurring.comment)

    router.callback_query.register(r.pick_weekday, F.data.startswith("recur:wd:"))
    router.callback_query.register(r.navigate_move, F.data.startswith("recur:cal:"))
    router.callback_query.register(r.pick_move_day, F.data.startswith("recur:day:"))
    router.callback_query.register(r.pick_slot, F.data.startswith("recur:slot:"))
    router.callback_query.register(r.ask_custom_time, F.data == "recur:other")
    router.callback_query.register(r.skip_comment, F.data == "recur:skipc")
    router.callback_query.register(r.cancel, F.data == "recur:cancel")
    router.callback_query.register(r.show_card, F.data.startswith("recur:card:"))
    router.callback_query.register(r.start_edit, F.data.startswith("recur:edit:"))
    router.callback_query.register(r.start_move, F.data.startswith("recur:move:"))
    router.callback_query.register(r.confirm_stop, F.data.startswith("recur:stopask:"))
    router.callback_query.register(r.do_stop, F.data.startswith("recur:stop:"))
    router.callback_query.register(r.confirm_skip, F.data.startswith("recur:skipask:"))
    router.callback_query.register(r.do_skip, F.data.startswith("recur:skip:"))


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="schedule")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = ScheduleHandlers(messages, session_factory)
    _register_recurring(router, messages, session_factory)

    router.message.register(h.show_feed, F.text == messages.schedule.button)
    router.message.register(h.apply_custom_time, Schedule.custom_time)
    router.message.register(h.apply_comment, Schedule.comment)

    router.callback_query.register(h.start_create, F.data.startswith("sched:new:"))
    router.callback_query.register(
        h.start_reschedule, F.data.startswith("sched:resch:")
    )
    router.callback_query.register(h.navigate, F.data.startswith("sched:cal:"))
    router.callback_query.register(h.noop, F.data == _CB_NOOP)
    router.callback_query.register(h.pick_day, F.data.startswith("sched:day:"))
    router.callback_query.register(h.pick_slot, F.data.startswith("sched:slot:"))
    router.callback_query.register(h.ask_custom_time, F.data == "sched:other")
    router.callback_query.register(h.choose_regular, F.data.startswith("sched:reg:"))
    router.callback_query.register(h.skip_comment, F.data == _CB_SKIP)
    router.callback_query.register(h.cancel, F.data == _CB_CANCEL)
    router.callback_query.register(h.open_day, F.data == _CB_FEED)
    router.callback_query.register(h.open_day, F.data.startswith("sched:day_view:"))
    router.callback_query.register(h.show_week, F.data == "sched:week")
    router.callback_query.register(h.show_history, F.data.startswith("sched:hist:"))
    router.callback_query.register(
        h.show_client_history, F.data.startswith("sched:chist:")
    )
    router.callback_query.register(h.show_card, F.data.startswith("sched:card:"))
    router.callback_query.register(h.confirm_delete, F.data.startswith("sched:del:"))
    router.callback_query.register(h.do_delete, F.data.startswith("sched:delyes:"))
    return router
