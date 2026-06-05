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
from src.bot.messages import BotMessages, ScheduleMessages
from src.domain.appointment import Appointment
from src.domain.schedule import (
    RU_MONTHS_NOMINATIVE,
    RU_WEEKDAYS_SHORT,
    format_ru_date,
    format_ru_short,
    generate_slots,
    parse_hhmm,
    today_in_tz,
    utc_to_wall,
)
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import (
    AppointmentsPage,
    DayGroup,
    HistoryWeek,
    create_appointment,
    delete_appointment,
    group_by_day,
    history_week_monday,
    list_client_history_page,
    list_specialist_day,
    list_specialist_history_week,
    list_specialist_week,
    reschedule_appointment,
    taken_slot_times,
)
from src.services.clients import client_name_map
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


# --- pure builders ------------------------------------------------------------


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * _MONTHS_IN_YEAR + (month - 1)) + delta
    return index // _MONTHS_IN_YEAR, index % _MONTHS_IN_YEAR + 1


def _noop_button(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=_CB_NOOP)


def _calendar_header(year: int, month: int, today: date) -> list[InlineKeyboardButton]:
    # Allow navigating back only to the current month; earlier months are useless
    # because every day in them is past and therefore inactive.
    if (year, month) > (today.year, today.month):
        py, pm = _shift_month(year, month, -1)
        prev_btn = InlineKeyboardButton(text="◀", callback_data=f"sched:cal:{py}:{pm}")
    else:
        prev_btn = _noop_button(" ")
    ny, nm = _shift_month(year, month, 1)
    return [
        prev_btn,
        _noop_button(f"{RU_MONTHS_NOMINATIVE[month]} {year}"),
        InlineKeyboardButton(text="▶", callback_data=f"sched:cal:{ny}:{nm}"),
    ]


def _day_button(year: int, month: int, day: int, today: date) -> InlineKeyboardButton:
    if day == 0:
        return _noop_button("·")
    current = date(year, month, day)
    if current < today:
        # Past day: shown but inert, so it cannot be selected (spec).
        return _noop_button(str(day))
    text = f"{_TODAY_MARK}{day}" if current == today else str(day)
    return InlineKeyboardButton(
        text=text, callback_data=f"sched:day:{year}:{month}:{day}"
    )


def build_calendar(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    rows = [_calendar_header(year, month, today)]
    rows.append([_noop_button(name) for name in RU_WEEKDAYS_SHORT])
    weeks = _calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    rows.extend(
        [_day_button(year, month, day, today) for day in week] for week in weeks
    )
    rows.append([InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)])
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
                    text=m.btn_delete, callback_data=f"sched:del:{appt.id}"
                ),
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        ]
    )


def _render_grouped(
    title: str,
    groups: list[DayGroup],
    names: dict[int, str],
    tz: str,
    m: ScheduleMessages,
) -> str:
    blocks: list[str] = []
    for group in groups:
        header = m.day_header.format(date=format_ru_date(group.day))
        lines = [
            m.line.format(
                child=names.get(appt.client_id, m.dash),
                time=f"{utc_to_wall(appt.starts_at, tz):%H:%M}",
                comment=_comment_part(appt.comment, m),
            )
            for appt in group.appointments
        ]
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


def _render_day(day: date, appts: list[Appointment], m: ScheduleMessages) -> str:
    # Day view = just the header; appointments live in the keyboard as buttons
    # (no duplicate text list).
    header = m.day_title.format(date=format_ru_date(day))
    return header if appts else f"{header}\n\n{m.day_empty}"


def _day_keyboard(
    day: date,
    appts: list[Appointment],
    names: dict[int, str],
    tz: str,
    m: ScheduleMessages,
) -> InlineKeyboardMarkup:
    back = f"sched:day_view:{day.isoformat()}"
    rows = [
        _card_button(
            appt,
            f"{utc_to_wall(appt.starts_at, tz):%H:%M} · "
            f"{names.get(appt.client_id, m.dash)}"
            f"{_comment_part(appt.comment, m)}",
            back,
        )
        for appt in appts
    ]
    prev_day = (day - timedelta(days=1)).isoformat()
    next_day = (day + timedelta(days=1)).isoformat()
    rows.extend(
        (
            [
                InlineKeyboardButton(
                    text="◀", callback_data=f"sched:day_view:{prev_day}"
                ),
                InlineKeyboardButton(
                    text="▶", callback_data=f"sched:day_view:{next_day}"
                ),
            ],
            [
                InlineKeyboardButton(text=m.btn_week, callback_data="sched:week"),
                InlineKeyboardButton(text=m.btn_history, callback_data="sched:hist:0"),
            ],
        )
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
    appointment_id: int, m: ScheduleMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_confirm_delete,
                    callback_data=f"sched:delyes:{appointment_id}",
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL, callback_data=f"sched:card:{appointment_id}"
                ),
            ]
        ]
    )


def _deleted_keyboard(m: ScheduleMessages) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m.btn_today, callback_data=_CB_FEED)]
        ]
    )


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


class ScheduleHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.schedule
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
            taken = await taken_slot_times(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                day=chosen,
                tz=specialist.timezone,
                exclude_id=data.get("appointment_id"),
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
            await state.update_data(hhmm=hhmm)
            await state.set_state(Schedule.comment)
            await target.answer(self._m.ask_comment, reply_markup=_skip_keyboard())
        else:
            await self._do_reschedule(target, state, specialist_id, hhmm)

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
        appt_id = _last_int(callback.data)
        await _callback_message(callback).edit_text(
            self._m.confirm_delete,
            reply_markup=_delete_confirm_keyboard(appt_id, self._m),
        )
        await callback.answer()

    async def do_delete(self, callback: CallbackQuery, specialist_id: int) -> None:
        appt_id = _last_int(callback.data)
        async with self._session_factory() as session:
            await delete_appointment(
                SqlAlchemyAppointmentsRepo(session),
                appointment_id=appt_id,
                specialist_id=specialist_id,
            )
        await _callback_message(callback).edit_text(
            self._m.deleted, reply_markup=_deleted_keyboard(self._m)
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
        if day is None:  # entry point lands on today in the specialist's timezone
            day = today_in_tz(datetime.now(UTC), tz)
        async with self._session_factory() as session:
            appts = await list_specialist_day(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                day=day,
                tz=tz,
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        return _render_day(day, appts, self._m), _day_keyboard(
            day, appts, names, tz, self._m
        )

    async def show_week(self, callback: CallbackQuery, specialist_id: int) -> None:
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        async with self._session_factory() as session:
            groups = await list_specialist_week(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                tz=tz,
                now=datetime.now(UTC),
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        text = (
            self._m.week_empty
            if not groups
            else _render_grouped(self._m.week_title, groups, names, tz, self._m)
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
        return _render_grouped(title, groups, names, tz, self._m)

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


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="schedule")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = ScheduleHandlers(messages, session_factory)

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
