import calendar as _calendar
from datetime import UTC, date, datetime, timedelta
from itertools import starmap
import logging
from typing import Any, cast

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

from src.bot.client_audit import record_client_message
from src.bot.handlers.clients import ClientsHandlers, SpecialistMiddleware
from src.bot.messages import (
    BotMessages,
    RecurringMessages,
    ReminderMessages,
    ScheduleMessages,
)
from src.bot.navigation import Navigator
from src.domain.appointment import Appointment
from src.domain.audit import AuditEvent, DeliveryStatus
from src.domain.client import Client
from src.domain.recurring import (
    RecurringSchedule,
    RecurringSlot,
    RecurringSlotOverride,
)
from src.domain.reminder import ReminderStatus
from src.domain.schedule import (
    RU_MONTHS_NOMINATIVE,
    RU_WEEKDAYS,
    RU_WEEKDAYS_EVERY,
    RU_WEEKDAYS_SHORT,
    format_ru_date,
    format_ru_short,
    generate_slots,
    next_occurrence_utc,
    next_weekday_on_or_after,
    occupied_grid_slots,
    parse_hhmm,
    parse_working_days,
    today_in_tz,
    utc_to_wall,
    wall_to_utc,
)
from src.domain.scheduled_message import (
    appointment_target_key,
    schedule_target_key,
    slot_date_target_key,
)
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo
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
    update_appointment_comment,
)
from src.services.clients import client_name_map
from src.services.message_templates import resolve_template
from src.services.recurring import (
    SeriesContext,
    add_slot,
    create_schedule,
    edit_slot,
    load_series_context,
    move_occurrence,
    occurrences_in_window,
    remove_slot,
    set_occurrence_comment,
    set_schedule_comment,
    skip_occurrence,
    stop_schedule,
)
from src.services.reminder import status_for_occurrence, statuses_for_appointments
from src.services.scheduled_messages import enqueue_deferred
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
# The recurring slot currently being edited — its own time is neither free nor taken.
_SLOT_CURRENT = "🟡"
# Today's cell in the calendar is highlighted (Telegram can't colour buttons).
_TODAY_MARK = "🟢"
_CB_NOOP = "sched:noop"
_CB_CANCEL = "sched:cancel"
_CB_FEED = "sched:feed"
_CB_SKIP = "sched:skip"
# "No" on the notify-the-client prompt (no colon after "ntfno" so it never matches
# the "sched:ntf:" prefix of the send action).
_CB_NOTIFY_NO = "sched:ntfno"


class Schedule(StatesGroup):
    custom_time = State()
    comment = State()  # comment at creation
    edit_comment = State()  # comment edit from the appointment card
    notify_custom_time = State()


async def _series_context(
    session: AsyncSession, specialist_id: int, tz: str
) -> SeriesContext:
    return await load_series_context(
        SqlAlchemyRecurringScheduleRepo(session),
        SqlAlchemyRecurringSlotRepo(session),
        SqlAlchemyRecurringSlotOverrideRepo(session),
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
                    text=m.btn_reschedule,
                    callback_data=f"sched:resch:{appt.id}~{back}",
                ),
                InlineKeyboardButton(
                    text=m.btn_delete, callback_data=f"sched:del:{appt.id}~{back}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=m.btn_comment, callback_data=f"sched:cmt:{appt.id}~{back}"
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
    rem: ReminderMessages,
    back: str,
    statuses: dict[tuple[int, datetime], ReminderStatus],
) -> list[InlineKeyboardButton]:
    # A virtual occurrence (id is None) routes to the single-meeting card by
    # slot_id/origin_date; a real row routes to its appointment card.
    child = names.get(appt.client_id, m.dash)
    time = f"{utc_to_wall(appt.starts_at, tz):%H:%M}"
    # Only a plain occurrence is marked 🔁; a moved one looks like a one-off.
    mark = f"{rm.mark} " if appt.recurring_mark else ""
    # ✅/❌ lead the row when the client confirmed/declined this occurrence.
    status = rem.status_mark(statuses.get((appt.client_id, appt.starts_at)))
    label = f"{status}{mark}{time} · {child}{_comment_part(appt.comment, m)}"
    if appt.id is None:
        assert appt.slot_id is not None  # noqa: S101 — virtual rows carry a slot
        assert appt.origin_date is not None  # noqa: S101
        # Carry the day view as the origin so the meeting card returns here.
        callback = f"recur:occ:{appt.slot_id}:{appt.origin_date.isoformat()}~{back}"
        return [InlineKeyboardButton(text=label, callback_data=callback)]
    return _card_button(appt, label, back)


def _render_day(day: date, appts: list[Appointment], m: ScheduleMessages) -> str:
    # Day view = just the header; appointments live in the keyboard as buttons
    # (no duplicate text list).
    header = m.day_title.format(date=format_ru_date(day))
    return header if appts else f"{header}\n\n{m.day_empty}"


def render_day_appointments_list(
    day: date,
    appts: list[Appointment],
    names: dict[int, str],
    tz: str,
    m: ScheduleMessages,
) -> str:
    """Text list of a day's appointments shown above the slot grid in pickers.

    An off-grid appointment (e.g. 14:10) appears here even though the grid has no
    button for its exact time — a safety net the slot markers alone cannot give.
    """
    title = m.day_list_title.format(date=format_ru_date(day))
    if not appts:
        return f"{title}\n{m.day_list_empty}"
    lines = [
        m.day_list_line.format(
            time=f"{utc_to_wall(appt.starts_at, tz):%H:%M}",
            child=names.get(appt.client_id, m.dash),
            comment=_comment_part(appt.comment, m),
        )
        for appt in sorted(appts, key=lambda appt: appt.starts_at)
    ]
    return "\n".join([title, *lines])


async def _picker_day_view(  # noqa: PLR0913
    session_factory: async_sessionmaker[AsyncSession],
    *,
    specialist: Specialist,
    day: date,
    grid: list[str],
    m: ScheduleMessages,
    exclude_id: int | None = None,
    exclude_slot_id: int | None = None,
) -> tuple[str, set[str]]:
    """Day's appointment list (text shown above the grid) + occupied grid cells.

    Shared by every slot picker: occupancy counts real bookings plus future repeats
    of active series (`exclude_id`/`exclude_slot_id` drop the appointment/slot being
    edited), then `occupied_grid_slots` widens each start to the grid cells whose
    interval it overlaps.
    """
    assert specialist.id is not None  # noqa: S101 — middleware guarantees existence
    tz = specialist.timezone
    async with session_factory() as session:
        series = await _series_context(session, specialist.id, tz)
        repo = SqlAlchemyAppointmentsRepo(session)
        taken = await taken_slot_times(
            repo,
            specialist_id=specialist.id,
            day=day,
            tz=tz,
            exclude_id=exclude_id,
            exclude_slot_id=exclude_slot_id,
            series=series,
        )
        appts = await list_specialist_day(
            repo, specialist_id=specialist.id, day=day, tz=tz, series=series
        )
        names = await client_name_map(
            SqlAlchemyClientsRepo(session), specialist_id=specialist.id
        )
    occupied = occupied_grid_slots(grid, taken, specialist.slot_minutes)
    return render_day_appointments_list(day, appts, names, tz, m), occupied


def _day_keyboard(  # noqa: PLR0913
    day: date,
    appts: list[Appointment],
    names: dict[int, str],
    tz: str,
    *,
    m: ScheduleMessages,
    rm: RecurringMessages,
    rem: ReminderMessages,
    prev_day: date | None,
    next_day: date | None,
    statuses: dict[tuple[int, datetime], ReminderStatus],
) -> InlineKeyboardMarkup:
    back = f"sched:day_view:{day.isoformat()}"
    rows = [
        _appt_row(
            appt,
            names,
            tz,
            m=m,
            rm=rm,
            rem=rem,
            back=back,
            statuses=statuses,
        )
        for appt in appts
    ]
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


async def _post_action(  # noqa: PLR0913
    navigator: Navigator | None,
    message: Message,
    *,
    result_text: str,
    back: str,
    specialist_id: int,
    edit: bool,
) -> None:
    # Thin module-level shim so both handler aggregators share one call site for the
    # post-action contract. The navigator is wired in build_router before any update.
    assert navigator is not None  # noqa: S101
    await navigator.open_after_action(
        message,
        result_text=result_text,
        back=back,
        specialist_id=specialist_id,
        edit=edit,
    )


async def _open_card(
    navigator: Navigator | None,
    target: Message,
    *,
    specialist_id: int,
    card_back: str,
) -> None:
    # Re-open the entity card as the freshest screen — done only once the whole
    # notify scenario has finished (or immediately when no notify step applies), so
    # the card never flashes mid-flow (see the post-action ordering decision).
    assert navigator is not None  # noqa: S101 — wired in build_router before any update
    text, keyboard = await navigator.render(specialist_id, card_back)
    await target.answer(text, reply_markup=keyboard)


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


# Notify-the-client moment sub-step callbacks (shown after "Yes"). The notify
# context (event, client, snapshot text, chat_id, target_key) is stashed in FSM
# data when the preview is shown — not in callback_data — so a rich target
# (series + date + key) never risks the 64-byte limit (see design.md, decision 8).
_CB_NOTIFY_WHEN = "sched:ntfwhen"  # "Yes" → reveal the moment choice
_CB_NOTIFY_NOW = "sched:ntfnow"  # send immediately (the old behaviour)
_CB_NOTIFY_PRESET = "sched:ntfpreset"  # defer to deferred_notify_time
_CB_NOTIFY_CUSTOM = "sched:ntfcustom"  # defer to a typed time
_CB_NOTIFY_CUSTOM_CANCEL = "sched:ntfcancel"  # back from typed time to moment choice
# FSM-data key holding the pending notification context between the preview and the
# moment choice.
_NOTIFY_DATA_KEY = "notify"


# A one-off notify event maps to a template_key that is also the ScheduleMessages
# attribute name holding its default — so getattr(m, key) is the default text.
_NOTIFY_KEYS = {
    "c": "notify_created",
    "r": "notify_rescheduled",
    "x": "notify_cancelled",
}

# Event letter → audit event, shared by one-off and series notifications. A series
# "modified" (m) is a schedule change, so it journals as rescheduled.
_NOTIFY_AUDIT_EVENTS = {
    "c": AuditEvent.NOTIFY_CREATED,
    "r": AuditEvent.NOTIFY_RESCHEDULED,
    "m": AuditEvent.NOTIFY_RESCHEDULED,
    "x": AuditEvent.NOTIFY_CANCELLED,
}


def _series_notify_key(event: str) -> str:
    # Create ("c") and per-slot changes ("m") describe the resulting weekly rule;
    # stop ("x") describes the cancellation. Each maps to its own editable template.
    if event == "c":
        return "notify_series_created"
    if event == "m":
        return "notify_series_changed"
    return "notify_series_cancelled"


async def _resolve_notify(
    session_factory: async_sessionmaker[AsyncSession],
    specialist_id: int,
    key: str,
    default: str,
) -> str:
    async with session_factory() as session:
        return await resolve_template(
            SqlAlchemyMessageTemplatesRepo(session),
            specialist_id=specialist_id,
            key=key,
            default=default,
        )


def _notify_text(template: str, starts_at: datetime, tz: str) -> str:
    wall = utc_to_wall(starts_at, tz)
    return template.format(date=format_ru_date(wall.date()), time=f"{wall:%H:%M}")


def _rule_line(weekday: int, hhmm: str) -> str:
    """`каждый вторник в 14:00` — one slot's rule, lower-cased to sit in a sentence."""
    every = RU_WEEKDAYS_EVERY[weekday]
    return f"{every[0].lower()}{every[1:]} в {hhmm}"


def _rule_text(slots: list[tuple[int, str]]) -> str:
    """Comma-joined rule for every slot, e.g. `каждый Пн в 12:00, каждый Вт в 14:00`."""
    return ", ".join(starmap(_rule_line, slots))


def _series_notify_text(template: str, rule_text: str, time_hhmm: str) -> str:
    # Both rule and time are passed; each template uses whichever it declares.
    return template.format(rule=rule_text, time=time_hhmm)


def _notify_ask_keyboard(m: ScheduleMessages) -> InlineKeyboardMarkup:
    # "Yes" reveals the moment choice (context read from FSM); "No" declines.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=m.notify_yes, callback_data=_CB_NOTIFY_WHEN),
                InlineKeyboardButton(text=m.notify_no, callback_data=_CB_NOTIFY_NO),
            ]
        ]
    )


def _notify_when_keyboard(
    preset_hhmm: str, m: ScheduleMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.notify_when_now, callback_data=_CB_NOTIFY_NOW
                ),
                InlineKeyboardButton(
                    text=m.notify_when_preset.format(time=preset_hhmm),
                    callback_data=_CB_NOTIFY_PRESET,
                ),
            ],
            [
                InlineKeyboardButton(
                    text=m.notify_when_custom, callback_data=_CB_NOTIFY_CUSTOM
                )
            ],
        ]
    )


def _notify_custom_cancel_keyboard(m: ScheduleMessages) -> InlineKeyboardMarkup:
    # "Отмена" on the typed-time step goes back to the moment choice, not the card —
    # the user already chose to notify, only the "how" is being reconsidered.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.notify_custom_cancel,
                    callback_data=_CB_NOTIFY_CUSTOM_CANCEL,
                )
            ]
        ]
    )


def _format_send_when(due_at: datetime, tz: str) -> str:
    """`6 июня в 20:00` — the deferred send moment in the specialist's timezone."""
    wall = utc_to_wall(due_at, tz)
    return f"{format_ru_short(wall.date())} в {wall:%H:%M}"


async def _store_notify(  # noqa: PLR0913
    state: FSMContext,
    *,
    event: str,
    client: Client,
    text: str,
    target_key: str,
    card_back: str,
) -> None:
    # Snapshot everything the moment sub-step needs. The preview text IS the snapshot
    # delivered later — no re-render at send time (see design.md, decision 2).
    # `card_back` is the navigator token of the entity card to re-open once the whole
    # notify scenario finishes (so the card is the last, freshest screen — no jump).
    assert client.id is not None  # noqa: S101 — persisted clients always have an id
    assert client.telegram_chat_id is not None  # noqa: S101 — linked, checked by caller
    await state.update_data(
        **{
            _NOTIFY_DATA_KEY: {
                "event": event,
                "client_id": client.id,
                "chat_id": client.telegram_chat_id,
                "text": text,
                "target_key": target_key,
                "card": card_back,
            }
        }
    )


async def _ask_notify(  # noqa: PLR0913, PLR0917
    target: Message,
    state: FSMContext,
    event: str,
    client: Client | None,
    starts_at: datetime,
    target_key: str,
    card_back: str,
    tz: str,
    specialist_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    m: ScheduleMessages,
) -> bool:
    # Final step after a one-off create/reschedule/delete (or a single series date):
    # offer to notify, but only for a linked client (nothing to send to otherwise).
    # Returns whether the prompt was shown; if not, the caller opens the card itself.
    if client is None or client.telegram_chat_id is None:
        return False
    key = _NOTIFY_KEYS[event]
    template = await _resolve_notify(
        session_factory, specialist_id, key, getattr(m, key)
    )
    preview = _notify_text(template, starts_at, tz)
    await _store_notify(
        state,
        event=event,
        client=client,
        text=preview,
        target_key=target_key,
        card_back=card_back,
    )
    await target.answer(
        m.notify_ask.format(text=preview), reply_markup=_notify_ask_keyboard(m)
    )
    return True


async def _ask_series_notify(  # noqa: PLR0913, PLR0917
    target: Message,
    state: FSMContext,
    event: str,
    client: Client | None,
    rule_text: str,
    time_hhmm: str,
    target_key: str,
    card_back: str,
    specialist_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    m: ScheduleMessages,
) -> bool:
    # Final step after a schedule create/edit/stop: the message describes the weekly
    # rule(s) ("каждый четверг в 14:00"), not a single date.
    if client is None or client.telegram_chat_id is None:
        return False
    key = _series_notify_key(event)
    template = await _resolve_notify(
        session_factory, specialist_id, key, getattr(m, key)
    )
    preview = _series_notify_text(template, rule_text, time_hhmm)
    await _store_notify(
        state,
        event=event,
        client=client,
        text=preview,
        target_key=target_key,
        card_back=card_back,
    )
    await target.answer(
        m.notify_ask.format(text=preview), reply_markup=_notify_ask_keyboard(m)
    )
    return True


async def _send_notify(  # noqa: PLR0913
    callback: CallbackQuery,
    chat_id: int,
    text: str,
    extra: dict[str, int],
    *,
    event: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    # Shared delivery for one-off and series notify "Yes": send, journal the
    # outcome, report success. Failure (client blocked the bot / chat gone) is
    # swallowed — the appointment operation stays applied; the caller shows the
    # outcome. The audit row is written either way (the "not delivered" fact too).
    assert callback.bot is not None  # noqa: S101 — callbacks always carry a bot
    audit_event = _NOTIFY_AUDIT_EVENTS[event]
    try:
        await callback.bot.send_message(chat_id, text)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("appointment.notify_failed", extra=extra)
        await record_client_message(
            session_factory,
            specialist_id=extra["specialist_id"],
            client_id=extra["client_id"],
            event=audit_event,
            text=text,
            status=DeliveryStatus.FAILED,
            error=str(exc),
        )
        return False
    logger.info("appointment.notified", extra=extra)
    await record_client_message(
        session_factory,
        specialist_id=extra["specialist_id"],
        client_id=extra["client_id"],
        event=audit_event,
        text=text,
        status=DeliveryStatus.SENT,
    )
    return True


# --- handlers -----------------------------------------------------------------


class ScheduleHandlers:  # noqa: PLR0904 — handler aggregator for the schedule router
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.schedule
        self._rm = messages.recurring
        self._rem = messages.reminder
        self._session_factory = session_factory
        # Set in build_router once every router's view builders exist.
        self._navigator: Navigator | None = None

    def set_navigator(self, navigator: Navigator) -> None:
        self._navigator = navigator

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

    async def _load_client(self, specialist_id: int, client_id: int) -> Client | None:
        async with self._session_factory() as session:
            return await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )

    async def _load_appt(
        self, appointment_id: int, specialist_id: int
    ) -> Appointment | None:
        async with self._session_factory() as session:
            return await SqlAlchemyAppointmentsRepo(session).get_for_specialist(
                appointment_id, specialist_id
            )

    # --- navigation builders (re-open a target from another router) -----------

    async def nav_day(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        # Serves "sched:day_view:<iso>", "sched:feed", and the unknown-target
        # fallback (anything that is not a concrete day → today's landing day).
        if back.startswith("sched:day_view:"):
            day: date | None = date.fromisoformat(back.rsplit(":", 1)[1])
        else:
            day = None
        return await self._day_view(specialist_id, day)

    async def nav_client_history(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        _, _, raw_client, raw_page = back.split(":")
        return await self._client_history_view(
            specialist_id, int(raw_client), int(raw_page)
        )

    async def nav_appt_card(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        # "sched:card:<id>[~<inner-back>]" → the appointment card. Used to re-open the
        # card as the last screen after a create/reschedule notify scenario finishes.
        head, _, inner = back.partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        async with self._session_factory() as session:
            appt = await SqlAlchemyAppointmentsRepo(session).get_for_specialist(
                appt_id, specialist_id
            )
        # Navigation only targets a card we just created/moved, so it still exists.
        assert appt is not None  # noqa: S101
        if not inner:  # default: the appointment's own day
            inner = (
                f"sched:day_view:{utc_to_wall(appt.starts_at, tz).date().isoformat()}"
            )
        child = await self._child_name(specialist_id, appt.client_id)
        async with self._session_factory() as session:
            status = await status_for_occurrence(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                client_id=appt.client_id,
                starts_at=appt.starts_at,
            )
        text = render_card(appt, child, tz, self._m)
        if status is ReminderStatus.CONFIRMED:
            text = f"{text}\n{self._rem.card_confirmed}"
        return text, _card_keyboard(appt, self._m, inner)

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
        # "sched:resch:<id>[~<origin-back>]" — keep the origin so the result returns
        # to wherever the card was opened from (client card / day), not the day feed.
        head, _, back = (callback.data or "").partition("~")
        await state.clear()
        await state.update_data(
            flow="reschedule", appointment_id=int(head.rsplit(":", 1)[1]), back=back
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
        day_list, occupied = await _picker_day_view(
            self._session_factory,
            specialist=specialist,
            day=chosen,
            grid=slots,
            m=self._m,
            exclude_id=data.get("appointment_id"),
        )
        await _callback_message(callback).edit_text(
            f"{day_list}\n\n{self._m.pick_time}",
            reply_markup=build_slots_keyboard(slots, occupied, self._m),
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
        if regular:
            # Seed the first slot from the picked date+time, then loop "add a day?".
            data = await state.get_data()
            day = date.fromisoformat(data["day"])
            await state.update_data(
                flow="rcreate",
                slots=[
                    {
                        "weekday": day.weekday(),
                        "hhmm": data["hhmm"],
                        "start_date": data["day"],
                    }
                ],
            )
            await _callback_message(callback).edit_text(
                self._rm.add_more, reply_markup=_add_more_keyboard(self._rm)
            )
            await callback.answer()
            return
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
        await self._finish_one_off(message, state, specialist_id, comment)

    async def skip_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await self._finish_one_off(
            _callback_message(callback), state, specialist_id, None
        )
        await callback.answer()

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
                audit=SqlAlchemyAuditRepo(session),
            )
        client = await self._load_client(specialist_id, data["client_id"])
        await state.clear()
        await target.answer(self._m.created)
        assert appt.id is not None  # noqa: S101 — created appointments have an id
        # Defer the appointment card to the end of the notify scenario (back to the
        # client card the creation started from).
        card_back = f"sched:card:{appt.id}~clients:card:{data['client_id']}"
        shown = await _ask_notify(
            target,
            state,
            "c",
            client,
            appt.starts_at,
            appointment_target_key(appt.id),
            card_back,
            specialist.timezone,
            specialist_id,
            self._session_factory,
            self._m,
        )
        if not shown:
            await _open_card(
                self._navigator,
                target,
                specialist_id=specialist_id,
                card_back=card_back,
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
                audit=SqlAlchemyAuditRepo(session),
            )
        await state.clear()
        if moved is None:
            await target.answer(self._m.not_found)
            return
        client = await self._load_client(specialist_id, moved.client_id)
        await target.answer(self._m.rescheduled)
        assert moved.id is not None  # noqa: S101 — rescheduled appointments have an id
        # Defer the appointment card to the end of the notify scenario; its Back
        # returns to the origin we came from (client card / day), else the new day.
        origin = data.get("back") or f"sched:day_view:{data['day']}"
        card_back = f"sched:card:{moved.id}~{origin}"
        shown = await _ask_notify(
            target,
            state,
            "r",
            client,
            moved.starts_at,
            appointment_target_key(moved.id),
            card_back,
            specialist.timezone,
            specialist_id,
            self._session_factory,
            self._m,
        )
        if not shown:
            await _open_card(
                self._navigator,
                target,
                specialist_id=specialist_id,
                card_back=card_back,
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
        async with self._session_factory() as session:
            status = await status_for_occurrence(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                client_id=appt.client_id,
                starts_at=appt.starts_at,
            )
        text = render_card(appt, child, tz, self._m)
        if status is ReminderStatus.CONFIRMED:
            text = f"{text}\n{self._rem.card_confirmed}"
        await _callback_message(callback).edit_text(
            text, reply_markup=_card_keyboard(appt, self._m, back)
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

    async def start_edit_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # "sched:cmt:<id>[~<origin-back>]" — edit a one-off appointment's comment.
        head, _, back = (callback.data or "").partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        appt = await self._load_appt(appt_id, specialist_id)
        if appt is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(
            flow="edit_comment",
            appointment_id=appt_id,
            card=f"sched:card:{appt_id}~{back}",
        )
        await state.set_state(Schedule.edit_comment)
        await _callback_message(callback).edit_text(
            self._m.ask_edit_comment, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def apply_edit_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        comment = (message.text or "").strip() or None
        async with self._session_factory() as session:
            saved = await update_appointment_comment(
                SqlAlchemyAppointmentsRepo(session),
                appointment_id=data["appointment_id"],
                specialist_id=specialist_id,
                comment=comment,
                now=datetime.now(UTC),
            )
        card = data.get("card") or ""
        await state.clear()
        if saved is None:
            await message.answer(self._m.not_found)
            return
        await message.answer(self._m.comment_set)
        await _open_card(
            self._navigator, message, specialist_id=specialist_id, card_back=card
        )

    async def do_delete(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, _, back = (callback.data or "").partition("~")
        appt_id = int(head.rsplit(":", 1)[1])
        async with self._session_factory() as session:
            repo = SqlAlchemyAppointmentsRepo(session)
            # Read the appointment before deleting it so the notify text (client +
            # time) can still be built afterwards (see design.md, decision 2).
            appt = await repo.get_for_specialist(appt_id, specialist_id)
            await delete_appointment(
                repo,
                appointment_id=appt_id,
                specialist_id=specialist_id,
                audit=SqlAlchemyAuditRepo(session),
                client_id=appt.client_id if appt is not None else None,
            )
        target = _callback_message(callback)
        # Turn the stale card into the "deleted" result; the entity card (day/client)
        # is re-opened only after the notify scenario, so nothing flashes mid-flow.
        await target.edit_text(self._m.deleted)
        shown = False
        if appt is not None and appt.id is not None:
            specialist = await self._load_settings(specialist_id)
            client = await self._load_client(specialist_id, appt.client_id)
            shown = await _ask_notify(
                target,
                state,
                "x",
                client,
                appt.starts_at,
                appointment_target_key(appt.id),
                back,
                specialist.timezone,
                specialist_id,
                self._session_factory,
                self._m,
            )
        if not shown:
            await _open_card(
                self._navigator, target, specialist_id=specialist_id, card_back=back
            )
        await callback.answer()

    # --- notify the client: choose the send moment ---------------------------

    async def ask_when(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # "Yes" on the notify prompt: reveal the Now / preset / custom-time choice.
        data = await state.get_data()
        if _NOTIFY_DATA_KEY not in data:
            await self._notify_stale(callback)
            return
        specialist = await self._load_settings(specialist_id)
        await _callback_message(callback).edit_text(
            self._m.notify_when_ask,
            reply_markup=_notify_when_keyboard(
                specialist.deferred_notify_time, self._m
            ),
        )
        await callback.answer()

    async def notify_now(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # "Сейчас": the original immediate path. Re-check the link at send time.
        notify = (await state.get_data()).get(_NOTIFY_DATA_KEY)
        if notify is None:
            await self._notify_stale(callback)
            return
        message = _callback_message(callback)
        client = await self._load_client(specialist_id, notify["client_id"])
        if client is None or client.telegram_chat_id is None:
            await state.clear()
            await message.edit_text(self._m.notify_not_linked)
            await _open_card(
                self._navigator,
                message,
                specialist_id=specialist_id,
                card_back=notify["card"],
            )
            await callback.answer()
            return
        extra = {"specialist_id": specialist_id, "client_id": notify["client_id"]}
        sent = await _send_notify(
            callback,
            client.telegram_chat_id,
            notify["text"],
            extra,
            event=notify["event"],
            session_factory=self._session_factory,
        )
        await state.clear()
        await message.edit_text(self._m.notify_sent if sent else self._m.notify_failed)
        await _open_card(
            self._navigator,
            message,
            specialist_id=specialist_id,
            card_back=notify["card"],
        )
        await callback.answer()

    async def notify_preset(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # "в HH:MM": queue for the preset deferred time (nearest occurrence).
        notify = (await state.get_data()).get(_NOTIFY_DATA_KEY)
        if notify is None:
            await self._notify_stale(callback)
            return
        specialist = await self._load_settings(specialist_id)
        await self._enqueue_and_report(
            _callback_message(callback),
            state,
            specialist,
            notify,
            specialist.deferred_notify_time,
            edit=True,
        )
        await callback.answer()

    async def notify_custom(self, callback: CallbackQuery, state: FSMContext) -> None:
        # "Своё время": ask for a typed HH:MM, then queue at its nearest occurrence.
        if _NOTIFY_DATA_KEY not in await state.get_data():
            await self._notify_stale(callback)
            return
        await state.set_state(Schedule.notify_custom_time)
        await _callback_message(callback).edit_text(
            self._m.notify_custom_time_ask,
            reply_markup=_notify_custom_cancel_keyboard(self._m),
        )
        await callback.answer()

    async def notify_custom_cancel(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # Back to the moment choice: drop only the input FSM state, keep the notify
        # context in FSM data so the user can pick another moment.
        await state.set_state(None)
        await self.ask_when(callback, state, specialist_id)

    async def apply_notify_custom_time(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        hhmm = parse_hhmm(message.text or "")
        if hhmm is None:
            # Invalid: stay in the state and re-ask (no row queued).
            await message.answer(self._m.bad_time)
            return
        notify = (await state.get_data()).get(_NOTIFY_DATA_KEY)
        if notify is None:  # pragma: no cover - the FSM state always carries notify
            await state.clear()
            await message.answer(self._m.notify_session_stale)
            return
        specialist = await self._load_settings(specialist_id)
        await self._enqueue_and_report(
            message, state, specialist, notify, hhmm, edit=False
        )

    async def _enqueue_and_report(  # noqa: PLR0913
        self,
        target: Message,
        state: FSMContext,
        specialist: Specialist,
        notify: dict[str, Any],
        hhmm: str,
        *,
        edit: bool,
    ) -> None:
        assert specialist.id is not None  # noqa: S101 — middleware guarantees existence
        tz = specialist.timezone
        now = datetime.now(UTC)
        due_at = next_occurrence_utc(hhmm, now, tz)
        async with self._session_factory() as session:
            result = await enqueue_deferred(
                SqlAlchemyScheduledMessagesRepo(session),
                specialist_id=specialist.id,
                client_id=notify["client_id"],
                chat_id=notify["chat_id"],
                text=notify["text"],
                target_key=notify["target_key"],
                event=_NOTIFY_AUDIT_EVENTS[notify["event"]],
                due_at=due_at,
                now=now,
            )
        text = self._m.notify_deferred_queued.format(when=_format_send_when(due_at, tz))
        if result.superseded_due_at is not None:
            replaced = _format_send_when(result.superseded_due_at, tz)
            text = f"{text} {self._m.notify_deferred_superseded.format(when=replaced)}"
        await state.clear()
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        await _open_card(
            self._navigator,
            target,
            specialist_id=specialist.id,
            card_back=notify["card"],
        )

    async def _notify_stale(self, callback: CallbackQuery) -> None:
        # FSM context lost (another operation cleared it): decline softly, send nothing.
        await _callback_message(callback).edit_text(self._m.notify_session_stale)
        await callback.answer()

    async def notify_skip(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # "No" on the notify prompt: replace it with a short confirmation, then return
        # to the entity card as the freshest screen.
        notify = (await state.get_data()).get(_NOTIFY_DATA_KEY)
        await state.clear()
        message = _callback_message(callback)
        await message.edit_text(self._m.notify_skipped)
        if notify is not None:
            await _open_card(
                self._navigator,
                message,
                specialist_id=specialist_id,
                card_back=notify["card"],
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
            statuses = await statuses_for_appointments(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                appointments=appts,
            )
            prev_day = await adjacent_shown_day(
                repo,
                specialist_id=specialist_id,
                working_days=working,
                tz=tz,
                day=day,
                forward=False,
                series=series,
            )
            next_day = await adjacent_shown_day(
                repo,
                specialist_id=specialist_id,
                working_days=working,
                tz=tz,
                day=day,
                forward=True,
                series=series,
            )
        return _render_day(day, appts, self._m), _day_keyboard(
            day,
            appts,
            names,
            tz,
            m=self._m,
            rm=self._rm,
            rem=self._rem,
            prev_day=prev_day,
            next_day=next_day,
            statuses=statuses,
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

    async def _client_history_view(
        self, specialist_id: int, client_id: int, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
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
        return text, _client_history_keyboard(client_id, page_obj)

    async def show_client_history(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        _, _, raw_client, raw_page = (callback.data or "").split(":")
        text, keyboard = await self._client_history_view(
            specialist_id, int(raw_client), int(raw_page)
        )
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()


_WEEKDAYS_PER_ROW = 4


class Recurring(StatesGroup):
    # `custom_time` and `comment`/`occ_comment` are the only message-input steps; all
    # other steps are callback-driven and dispatch on the FSM `flow` value.
    custom_time = State()
    comment = State()  # schedule-level comment (creation)
    sched_comment = State()  # schedule-level comment (edit from the schedule card)
    occ_comment = State()  # single-occurrence comment


# --- pure builders ------------------------------------------------------------


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


def _recur_slot_marker(slot: str, taken: set[str], current: str | None) -> str:
    # The edited slot's own time wins over taken/free so it reads as "current".
    if slot == current:
        return _SLOT_CURRENT
    return _SLOT_TAKEN if slot in taken else _SLOT_FREE


def build_recur_slots_keyboard(
    slots: list[str],
    taken: set[str],
    m: ScheduleMessages,
    *,
    current: str | None = None,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{_recur_slot_marker(slot, taken, current)} {slot}",
            callback_data=f"recur:tslot:{slot.replace(':', '')}",
        )
        for slot in slots
    ]
    rows = [
        buttons[i : i + _SLOTS_PER_ROW] for i in range(0, len(buttons), _SLOTS_PER_ROW)
    ]
    rows.extend(
        (
            [InlineKeyboardButton(text=m.btn_other_time, callback_data="recur:tother")],
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")],
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _add_more_keyboard(m: RecurringMessages) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m.btn_add_more, callback_data="recur:add")],
            [InlineKeyboardButton(text=m.btn_done, callback_data="recur:done")],
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")],
        ]
    )


def _schedule_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data="recur:cskipc"),
                InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel"),
            ]
        ]
    )


def _recur_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data="recur:cancel")]
        ]
    )


# --- formatting ---------------------------------------------------------------


def _rule_lines(slots: list[RecurringSlot], rm: RecurringMessages) -> str:
    """One `🔁 каждый вторник в 14:00` line per active slot, newline-joined."""
    return "\n".join(
        rm.rule_line.format(weekday=RU_WEEKDAYS_EVERY[s.weekday], time=s.time_hhmm)
        for s in slots
    )


def render_schedule_card(  # noqa: PLR0913, PLR0917
    schedule: RecurringSchedule,
    child: str,
    slots: list[RecurringSlot],
    occurrences: list[Appointment],
    rm: RecurringMessages,
    dash: str,
) -> str:
    text = rm.schedule_card.format(
        child=child,
        comment=schedule.comment or dash,
        rule=_rule_lines(slots, rm) or dash,
    )
    if not occurrences:
        text = f"{text}\n\n{rm.empty_window}"
    return text


def render_meeting_card(  # noqa: PLR0913, PLR0917
    child: str,
    occ_date: date,
    occ_time: str,
    comment: str | None,
    rm: RecurringMessages,
    dash: str,
) -> str:
    return rm.meeting_card.format(
        child=child,
        date=format_ru_date(occ_date),
        time=occ_time,
        comment=comment or dash,
    )


def _schedule_card_keyboard(
    schedule_id: int,
    occurrences: list[Appointment],
    tz: str,
    rm: RecurringMessages,
    back: str,
) -> InlineKeyboardMarkup:
    # One button per upcoming occurrence (rolling 14 days); each opens its meeting
    # card and returns here. Then configure / cancel-all, then back.
    rows: list[list[InlineKeyboardButton]] = []
    self_back = f"recur:sched:{schedule_id}"
    for occ in occurrences:
        assert occ.slot_id is not None  # noqa: S101 — occurrences carry a slot
        assert occ.origin_date is not None  # noqa: S101
        wall = utc_to_wall(occ.starts_at, tz)
        label = rm.occ_btn.format(
            date=format_ru_short(wall.date()),
            weekday=RU_WEEKDAYS[wall.date().weekday()],
            time=f"{wall:%H:%M}",
        )
        cb = f"recur:occ:{occ.slot_id}:{occ.origin_date.isoformat()}~{self_back}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.extend(
        (
            [
                InlineKeyboardButton(
                    text=rm.btn_schedule_comment,
                    callback_data=_with_back(f"recur:schedcmt:{schedule_id}", back),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rm.btn_configure, callback_data=f"recur:cfg:{schedule_id}"
                ),
                InlineKeyboardButton(
                    text=rm.btn_stop,
                    callback_data=_with_back(f"recur:stopask:{schedule_id}", back),
                ),
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _meeting_card_keyboard(
    slot_id: int,
    origin_date: date,
    schedule_id: int,
    rm: RecurringMessages,
    back: str,
) -> InlineKeyboardMarkup:
    occ = _with_back(f"{slot_id}:{origin_date.isoformat()}", back)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_move, callback_data=f"recur:occmove:{occ}"
                ),
                InlineKeyboardButton(
                    text=rm.btn_skip, callback_data=f"recur:occskipask:{occ}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rm.btn_comment, callback_data=f"recur:occcmt:{occ}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rm.btn_to_schedule,
                    callback_data=_with_back(f"recur:sched:{schedule_id}", back),
                ),
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        ]
    )


def _config_keyboard(
    schedule_id: int, slots: list[RecurringSlot], rm: RecurringMessages
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for slot in slots:
        label = rm.slot_btn.format(
            weekday=RU_WEEKDAYS[slot.weekday], time=slot.time_hhmm
        )
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"recur:slot:{slot.id}")]
        )
    rows.extend(
        (
            [
                InlineKeyboardButton(
                    text=rm.btn_add_day, callback_data=f"recur:cfgadd:{schedule_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=_BTN_BACK, callback_data=f"recur:sched:{schedule_id}"
                )
            ],
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _slot_actions_keyboard(
    slot: RecurringSlot, rm: RecurringMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_slot_time, callback_data=f"recur:slottime:{slot.id}"
                ),
                InlineKeyboardButton(
                    text=rm.btn_slot_day, callback_data=f"recur:slotday:{slot.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=rm.btn_slot_delete, callback_data=f"recur:slotdel:{slot.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=_BTN_BACK,
                    callback_data=f"recur:cfg:{slot.schedule_id}",
                )
            ],
        ]
    )


def _stop_confirm_keyboard(
    schedule_id: int, back: str, rm: RecurringMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_confirm_stop,
                    callback_data=_with_back(f"recur:stop:{schedule_id}", back),
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL,
                    callback_data=_with_back(f"recur:sched:{schedule_id}", back),
                ),
            ]
        ]
    )


def _occ_skip_confirm_keyboard(
    slot_id: int, origin_date: date, back: str, rm: RecurringMessages
) -> InlineKeyboardMarkup:
    occ = _with_back(f"{slot_id}:{origin_date.isoformat()}", back)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=rm.btn_confirm_skip, callback_data=f"recur:occskip:{occ}"
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL, callback_data=f"recur:occ:{occ}"
                ),
            ]
        ]
    )


def _with_back(head: str, back: str) -> str:
    """Append `~back` to a callback only when `back` is non-empty (no dangling `~`)."""
    return f"{head}~{back}" if back else head


def _split_back(callback_data: str | None) -> tuple[str, str]:
    """Split `head~back` callback data into `(head, back)`; back is `''` if absent."""
    head, _, back = (callback_data or "").partition("~")
    return head, back


def _parse_slot_date(head: str) -> tuple[int, date]:
    # "recur:<action>:<slot_id>:<origin_date>" — date is a plain ISO day (no colon).
    _, _, raw_id, raw_date = head.split(":", 3)
    return int(raw_id), date.fromisoformat(raw_date)


class RecurringHandlers:  # noqa: PLR0904 — handler aggregator for the recurring router
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.recurring
        self._sm = messages.schedule
        self._rem = messages.reminder
        self._session_factory = session_factory
        # Set in build_router once every router's view builders exist.
        self._navigator: Navigator | None = None

    def set_navigator(self, navigator: Navigator) -> None:
        self._navigator = navigator

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

    async def _load_client(self, specialist_id: int, client_id: int) -> Client | None:
        async with self._session_factory() as session:
            return await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )

    async def _load_schedule(
        self, schedule_id: int, specialist_id: int
    ) -> RecurringSchedule | None:
        async with self._session_factory() as session:
            return await SqlAlchemyRecurringScheduleRepo(session).get_for_specialist(
                schedule_id, specialist_id
            )

    async def _load_slot(
        self, slot_id: int, specialist_id: int
    ) -> RecurringSlot | None:
        async with self._session_factory() as session:
            return await SqlAlchemyRecurringSlotRepo(session).get_for_specialist(
                slot_id, specialist_id
            )

    async def _load_slots(self, schedule_id: int) -> list[RecurringSlot]:
        async with self._session_factory() as session:
            return await SqlAlchemyRecurringSlotRepo(session).list_for_schedule(
                schedule_id
            )

    async def _load_override(
        self, slot_id: int, origin_date: date
    ) -> RecurringSlotOverride | None:
        async with self._session_factory() as session:
            overrides = await SqlAlchemyRecurringSlotOverrideRepo(
                session
            ).list_for_slot(slot_id)
        return next((o for o in overrides if o.original_date == origin_date), None)

    # --- creation wizard (entered from the appointment flow's "make regular") ---

    async def add_day(self, callback: CallbackQuery) -> None:
        # "Добавить день": pick the next slot's weekday (flow stays "rcreate").
        await _callback_message(callback).edit_text(
            self._m.pick_weekday, reply_markup=_weekday_keyboard()
        )
        await callback.answer()

    async def done_adding(self, callback: CallbackQuery, state: FSMContext) -> None:
        # "Готово": move on to the shared schedule comment.
        await state.set_state(Recurring.comment)
        await _callback_message(callback).edit_text(
            self._m.ask_comment, reply_markup=_schedule_comment_keyboard()
        )
        await callback.answer()

    async def apply_schedule_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        comment = (message.text or "").strip() or None
        await self._finish_create(message, state, specialist_id, comment)

    async def skip_schedule_comment(
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
        client_id = data["client_id"]
        slots = data["slots"]
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            schedule = await create_schedule(
                SqlAlchemyRecurringScheduleRepo(session),
                specialist_id=specialist_id,
                client_id=client_id,
                comment=comment,
                now=now,
            )
        assert schedule.id is not None  # noqa: S101 — saved schedules have an id
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            slot_repo = SqlAlchemyRecurringSlotRepo(session)
            for s in slots:
                await add_slot(
                    slot_repo,
                    schedule_id=schedule.id,
                    weekday=s["weekday"],
                    time_hhmm=s["hhmm"],
                    tz=specialist.timezone,
                    now=now,
                    start_date=date.fromisoformat(s["start_date"]),
                )
        client = await self._load_client(specialist_id, client_id)
        await state.clear()
        await target.answer(self._m.created)
        rule = _rule_text([(s["weekday"], s["hhmm"]) for s in slots])
        card_back = f"recur:sched:{schedule.id}~clients:card:{client_id}"
        shown = await _ask_series_notify(
            target,
            state,
            "c",
            client,
            rule,
            slots[0]["hhmm"],
            schedule_target_key(schedule.id),
            card_back,
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator,
                target,
                specialist_id=specialist_id,
                card_back=card_back,
            )

    # --- shared weekday / time pickers (dispatch on flow) --------------------

    async def pick_weekday(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        weekday = _last_int(callback.data)
        await state.update_data(weekday=weekday)
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        grid = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        # Occupancy is computed on the date the slot will actually land on — the
        # nearest matching weekday ≥ today, the same one add_slot picks for start_date.
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        target_day = next_weekday_on_or_after(today, weekday)
        # Only editing a slot's day (cfg_day) excludes its own contribution and marks
        # its current time; creation flows (rcreate / cfg_add) show plain 🟢/🔴.
        exclude_slot_id, current = self._edit_slot_markers(data, weekday)
        day_list, occupied = await _picker_day_view(
            self._session_factory,
            specialist=specialist,
            day=target_day,
            grid=grid,
            m=self._sm,
            exclude_slot_id=exclude_slot_id,
        )
        await _callback_message(callback).edit_text(
            f"{day_list}\n\n{self._sm.pick_time}",
            reply_markup=build_recur_slots_keyboard(
                grid, occupied, self._sm, current=current
            ),
        )
        await callback.answer()

    @staticmethod
    def _edit_slot_markers(
        data: dict[str, Any], weekday: int
    ) -> tuple[int | None, str | None]:
        # cfg_day keeps the slot's time and only moves its weekday, so its current
        # time (stashed by start_slot_day) is highlighted on the target day — but only
        # when the chosen weekday is the slot's own (otherwise it lands elsewhere).
        if data.get("flow") != "cfg_day":
            return None, None
        current = data["edit_time"] if data.get("edit_weekday") == weekday else None
        return data["slot_id"], current

    async def pick_slot(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        raw = (callback.data or "").rsplit(":", 1)[1]
        hhmm = f"{raw[:2]}:{raw[2:]}"
        await self._apply_time(
            _callback_message(callback), state, specialist_id, hhmm, edit=True
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
        await self._apply_time(message, state, specialist_id, hhmm, edit=False)

    async def _apply_time(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        hhmm: str,
        *,
        edit: bool,
    ) -> None:
        data = await state.get_data()
        flow = data.get("flow")
        if flow == "rcreate":
            await self._append_slot(target, state, specialist_id, hhmm)
        elif flow == "cfg_add":
            await self._cfg_add_slot(target, state, specialist_id, hhmm)
        elif flow in {"cfg_time", "cfg_day"}:
            await self._cfg_edit_slot(target, state, specialist_id, hhmm)
        else:  # move
            await self._do_move(target, state, specialist_id, hhmm, edit=edit)

    async def _append_slot(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        weekday = data["weekday"]
        start = next_weekday_on_or_after(
            today_in_tz(datetime.now(UTC), specialist.timezone), weekday
        )
        slots = [
            *data["slots"],
            {"weekday": weekday, "hhmm": hhmm, "start_date": start.isoformat()},
        ]
        await state.update_data(slots=slots)
        await target.answer(self._m.add_more, reply_markup=_add_more_keyboard(self._m))

    # --- schedule card -------------------------------------------------------

    async def _render_schedule_card(
        self, specialist_id: int, schedule: RecurringSchedule, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        assert schedule.id is not None  # noqa: S101 — loaded schedules have an id
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        today = today_in_tz(datetime.now(UTC), tz)
        slots = await self._load_slots(schedule.id)
        async with self._session_factory() as session:
            override_repo = SqlAlchemyRecurringSlotOverrideRepo(session)
            overrides = {
                slot.id: await override_repo.list_for_slot(slot.id) for slot in slots
            }
        occurrences = occurrences_in_window(
            schedule, slots, overrides, today, today + timedelta(days=14), tz, today
        )
        child = await self._child_name(specialist_id, schedule.client_id)
        back = back or f"clients:card:{schedule.client_id}"
        text = render_schedule_card(
            schedule, child, slots, occurrences, self._m, self._sm.dash
        )
        return text, _schedule_card_keyboard(
            schedule.id, occurrences, tz, self._m, back
        )

    async def nav_schedule_card(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        head, inner = _split_back(back)
        schedule_id = _last_int(head)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        # Navigation only targets a schedule we just acted on, so it still exists.
        assert schedule is not None  # noqa: S101
        return await self._render_schedule_card(specialist_id, schedule, inner)

    async def show_schedule(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        schedule_id = _last_int(head)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        if schedule is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._render_schedule_card(specialist_id, schedule, back)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    # --- single-meeting card -------------------------------------------------

    async def _render_meeting_card(
        self, specialist_id: int, slot: RecurringSlot, origin_date: date, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        assert slot.id is not None  # noqa: S101 — loaded slots have an id
        specialist = await self._load_settings(specialist_id)
        tz = specialist.timezone
        schedule = await self._load_schedule(slot.schedule_id, specialist_id)
        assert schedule is not None  # noqa: S101 — owned slot has an owned schedule
        override = await self._load_override(slot.id, origin_date)
        back = back or f"sched:day_view:{origin_date.isoformat()}"
        child = await self._child_name(specialist_id, schedule.client_id)
        if override is not None and override.moved_to is not None:
            wall = utc_to_wall(override.moved_to, tz)
            occ_date, occ_time, starts_at = (
                wall.date(),
                f"{wall:%H:%M}",
                override.moved_to,
            )
        else:
            occ_date, occ_time = origin_date, slot.time_hhmm
            starts_at = wall_to_utc(origin_date, slot.time_hhmm, tz)
        comment = (
            override.comment
            if override is not None and override.comment is not None
            else schedule.comment
        )
        text = render_meeting_card(
            child, occ_date, occ_time, comment, self._m, self._sm.dash
        )
        async with self._session_factory() as session:
            status = await status_for_occurrence(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                client_id=schedule.client_id,
                starts_at=starts_at,
            )
        if status is ReminderStatus.CONFIRMED:
            text = f"{text}\n{self._rem.card_confirmed}"
        return text, _meeting_card_keyboard(
            slot.id, origin_date, slot.schedule_id, self._m, back
        )

    async def nav_meeting_card(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        head, inner = _split_back(back)
        slot_id, origin_date = _parse_slot_date(head)
        slot = await self._load_slot(slot_id, specialist_id)
        assert slot is not None  # noqa: S101 — navigation targets an existing slot
        return await self._render_meeting_card(specialist_id, slot, origin_date, inner)

    async def show_meeting(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        slot_id, origin_date = _parse_slot_date(head)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._render_meeting_card(
            specialist_id, slot, origin_date, back
        )
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    # --- configure: per-slot edit, add, delete -------------------------------

    async def _render_config(
        self, schedule_id: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        slots = await self._load_slots(schedule_id)
        return self._m.configure_title, _config_keyboard(schedule_id, slots, self._m)

    async def show_config(self, callback: CallbackQuery, specialist_id: int) -> None:
        schedule_id = _last_int(callback.data)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        if schedule is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._render_config(schedule_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def show_slot_actions(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        slot_id = _last_int(callback.data)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.slot_actions_title.format(
                weekday=RU_WEEKDAYS[slot.weekday], time=slot.time_hhmm
            ),
            reply_markup=_slot_actions_keyboard(slot, self._m),
        )
        await callback.answer()

    async def start_slot_time(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        slot_id = _last_int(callback.data)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        # Time-only edit keeps the slot's weekday; jump straight to the time picker.
        await state.update_data(flow="cfg_time", slot_id=slot_id, weekday=slot.weekday)
        specialist = await self._load_settings(specialist_id)
        grid = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        # Occupancy on the nearest date of the slot's own weekday; its own time is
        # marked "current" and excluded from the taken set.
        today = today_in_tz(datetime.now(UTC), specialist.timezone)
        target_day = next_weekday_on_or_after(today, slot.weekday)
        day_list, occupied = await _picker_day_view(
            self._session_factory,
            specialist=specialist,
            day=target_day,
            grid=grid,
            m=self._sm,
            exclude_slot_id=slot.id,
        )
        await _callback_message(callback).edit_text(
            f"{day_list}\n\n{self._sm.pick_time}",
            reply_markup=build_recur_slots_keyboard(
                grid, occupied, self._sm, current=slot.time_hhmm
            ),
        )
        await callback.answer()

    async def start_slot_day(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        slot_id = _last_int(callback.data)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        # Stash the slot's current weekday/time so pick_weekday can mark its own time
        # 🟡 when the chosen day is unchanged, without re-loading the slot.
        await state.update_data(
            flow="cfg_day",
            slot_id=slot_id,
            edit_weekday=slot.weekday,
            edit_time=slot.time_hhmm,
        )
        await _callback_message(callback).edit_text(
            self._m.pick_weekday, reply_markup=_weekday_keyboard()
        )
        await callback.answer()

    async def _cfg_edit_slot(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        async with self._session_factory() as session:
            slot = await edit_slot(
                SqlAlchemyRecurringSlotRepo(session),
                slot_id=data["slot_id"],
                specialist_id=specialist_id,
                weekday=data["weekday"],
                time_hhmm=hhmm,
                now=datetime.now(UTC),
                tz=specialist.timezone,
            )
        await state.clear()
        if slot is None:
            await target.answer(self._m.not_found)
            return
        await target.answer(self._m.edited)
        await self._ask_series_change(target, state, specialist_id, slot.schedule_id)

    async def start_cfg_add(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        schedule_id = _last_int(callback.data)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        if schedule is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(flow="cfg_add", schedule_id=schedule_id)
        await _callback_message(callback).edit_text(
            self._m.pick_weekday, reply_markup=_weekday_keyboard()
        )
        await callback.answer()

    async def _cfg_add_slot(
        self, target: Message, state: FSMContext, specialist_id: int, hhmm: str
    ) -> None:
        data = await state.get_data()
        specialist = await self._load_settings(specialist_id)
        schedule_id = data["schedule_id"]
        async with self._session_factory() as session:
            await add_slot(
                SqlAlchemyRecurringSlotRepo(session),
                schedule_id=schedule_id,
                weekday=data["weekday"],
                time_hhmm=hhmm,
                tz=specialist.timezone,
                now=datetime.now(UTC),
            )
        await state.clear()
        await target.answer(self._m.edited)
        await self._ask_series_change(target, state, specialist_id, schedule_id)

    async def delete_slot(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        slot_id = _last_int(callback.data)
        async with self._session_factory() as session:
            slot = await remove_slot(
                SqlAlchemyRecurringScheduleRepo(session),
                SqlAlchemyRecurringSlotRepo(session),
                slot_id=slot_id,
                specialist_id=specialist_id,
                now=datetime.now(UTC),
            )
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        target = _callback_message(callback)
        await callback.answer()
        await target.edit_text(self._m.slot_removed)
        schedule = await self._load_schedule(slot.schedule_id, specialist_id)
        # remove_slot stops the schedule when the last active slot is removed; the
        # loaded schedule is still readable (no active filter) but now inactive. A
        # still-active schedule is a change; a stopped one is a cancellation.
        if schedule is None or not schedule.active:
            await self._ask_series_stop(target, state, specialist_id, slot)
        else:
            await self._ask_series_change(
                target, state, specialist_id, slot.schedule_id
            )

    # --- offer to notify the client about a configure change -----------------

    async def _ask_series_change(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        schedule_id: int,
    ) -> None:
        # After a per-slot edit/add/delete that keeps the schedule active: offer to
        # tell the client the new weekly rule (event "m" → notify_series_changed).
        schedule = await self._load_schedule(schedule_id, specialist_id)
        assert schedule is not None  # noqa: S101 — just-edited schedule still exists
        slots = await self._load_slots(schedule_id)
        client = await self._load_client(specialist_id, schedule.client_id)
        card_back = f"recur:sched:{schedule_id}"
        shown = await _ask_series_notify(
            target,
            state,
            "m",
            client,
            _rule_text([(s.weekday, s.time_hhmm) for s in slots]),
            slots[0].time_hhmm if slots else "",
            schedule_target_key(schedule_id),
            card_back,
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator,
                target,
                specialist_id=specialist_id,
                card_back=card_back,
            )

    async def _ask_series_stop(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        slot: RecurringSlot,
    ) -> None:
        # The removed slot was the last active one, so the schedule is now stopped:
        # offer the cancellation notice (event "x"), described by that slot's time.
        schedule = await self._load_schedule(slot.schedule_id, specialist_id)
        client = (
            await self._load_client(specialist_id, schedule.client_id)
            if schedule is not None
            else None
        )
        shown = await _ask_series_notify(
            target,
            state,
            "x",
            client,
            _rule_text([(slot.weekday, slot.time_hhmm)]),
            slot.time_hhmm,
            schedule_target_key(slot.schedule_id),
            "",
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator, target, specialist_id=specialist_id, card_back=""
            )

    # --- stop the whole schedule ---------------------------------------------

    async def confirm_stop(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        schedule_id = _last_int(head)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        if schedule is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.confirm_stop,
            reply_markup=_stop_confirm_keyboard(schedule_id, back, self._m),
        )
        await callback.answer()

    async def do_stop(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        schedule_id = _last_int(head)
        slots = await self._load_slots(schedule_id)
        async with self._session_factory() as session:
            stopped = await stop_schedule(
                SqlAlchemyRecurringScheduleRepo(session),
                schedule_id=schedule_id,
                specialist_id=specialist_id,
                now=datetime.now(UTC),
            )
        if stopped is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        target = _callback_message(callback)
        await target.edit_text(self._m.stopped)
        client = await self._load_client(specialist_id, stopped.client_id)
        rule = _rule_text([(s.weekday, s.time_hhmm) for s in slots])
        first_time = slots[0].time_hhmm if slots else ""
        shown = await _ask_series_notify(
            target,
            state,
            "x",
            client,
            rule,
            first_time,
            schedule_target_key(schedule_id),
            back,
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator, target, specialist_id=specialist_id, card_back=back
            )
        await callback.answer()

    # --- move a single occurrence: pick new date, then time ------------------

    async def start_move(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        slot_id, origin_date = _parse_slot_date(head)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(
            flow="move",
            slot_id=slot_id,
            origin_date=origin_date.isoformat(),
            back=back,
            card=_with_back(f"recur:occ:{slot_id}:{origin_date.isoformat()}", back),
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
        grid = generate_slots(
            specialist.day_start, specialist.day_end, specialist.slot_minutes
        )
        day_list, occupied = await _picker_day_view(
            self._session_factory,
            specialist=specialist,
            day=chosen,
            grid=grid,
            m=self._sm,
        )
        await _callback_message(callback).edit_text(
            f"{day_list}\n\n{self._sm.pick_time}",
            reply_markup=build_recur_slots_keyboard(grid, occupied, self._sm),
        )
        await callback.answer()

    async def _do_move(
        self,
        target: Message,
        state: FSMContext,
        specialist_id: int,
        hhmm: str,
        *,
        edit: bool,
    ) -> None:
        data = await state.get_data()
        origin_date = date.fromisoformat(data["origin_date"])
        new_day = date.fromisoformat(data["new_day"])
        specialist = await self._load_settings(specialist_id)
        moved_to = wall_to_utc(new_day, hhmm, specialist.timezone)
        async with self._session_factory() as session:
            moved = await move_occurrence(
                SqlAlchemyRecurringSlotRepo(session),
                SqlAlchemyRecurringSlotOverrideRepo(session),
                slot_id=data["slot_id"],
                specialist_id=specialist_id,
                original_date=origin_date,
                moved_to=moved_to,
                now=datetime.now(UTC),
            )
        card = data.get("card") or ""
        await state.clear()
        if moved is None:
            await target.answer(self._m.not_found)
            return
        if edit:
            await target.edit_text(self._m.moved)
        else:
            await target.answer(self._m.moved)
        slot = await self._load_slot(data["slot_id"], specialist_id)
        assert slot is not None  # noqa: S101 — move succeeded, so the slot exists
        schedule = await self._load_schedule(slot.schedule_id, specialist_id)
        assert schedule is not None  # noqa: S101
        client = await self._load_client(specialist_id, schedule.client_id)
        shown = await _ask_notify(
            target,
            state,
            "r",
            client,
            moved_to,
            slot_date_target_key(data["slot_id"], origin_date),
            card,
            specialist.timezone,
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator, target, specialist_id=specialist_id, card_back=card
            )

    # --- skip a single occurrence --------------------------------------------

    async def confirm_skip(self, callback: CallbackQuery, specialist_id: int) -> None:
        head, back = _split_back(callback.data)
        slot_id, origin_date = _parse_slot_date(head)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.skip_confirm.format(date=format_ru_date(origin_date)),
            reply_markup=_occ_skip_confirm_keyboard(
                slot_id, origin_date, back, self._m
            ),
        )
        await callback.answer()

    async def do_skip(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        slot_id, origin_date = _parse_slot_date(head)
        async with self._session_factory() as session:
            skipped = await skip_occurrence(
                SqlAlchemyRecurringSlotRepo(session),
                SqlAlchemyRecurringSlotOverrideRepo(session),
                slot_id=slot_id,
                specialist_id=specialist_id,
                original_date=origin_date,
                now=datetime.now(UTC),
            )
        if skipped is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        target = _callback_message(callback)
        await target.edit_text(self._m.skipped)
        specialist = await self._load_settings(specialist_id)
        slot = await self._load_slot(slot_id, specialist_id)
        assert slot is not None  # noqa: S101 — skip succeeded, so the slot exists
        schedule = await self._load_schedule(slot.schedule_id, specialist_id)
        assert schedule is not None  # noqa: S101
        client = await self._load_client(specialist_id, schedule.client_id)
        starts_at = wall_to_utc(origin_date, slot.time_hhmm, specialist.timezone)
        shown = await _ask_notify(
            target,
            state,
            "x",
            client,
            starts_at,
            slot_date_target_key(slot_id, origin_date),
            back,
            specialist.timezone,
            specialist_id,
            self._session_factory,
            self._sm,
        )
        if not shown:
            await _open_card(
                self._navigator, target, specialist_id=specialist_id, card_back=back
            )
        await callback.answer()

    # --- edit the series' shared comment from the schedule card --------------

    async def start_sched_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        schedule_id = _last_int(head)
        schedule = await self._load_schedule(schedule_id, specialist_id)
        if schedule is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(
            flow="sched_comment",
            schedule_id=schedule_id,
            card=_with_back(f"recur:sched:{schedule_id}", back),
        )
        await state.set_state(Recurring.sched_comment)
        await _callback_message(callback).edit_text(
            self._m.ask_schedule_comment, reply_markup=_recur_cancel_keyboard()
        )
        await callback.answer()

    async def apply_sched_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        comment = (message.text or "").strip() or None
        async with self._session_factory() as session:
            saved = await set_schedule_comment(
                SqlAlchemyRecurringScheduleRepo(session),
                schedule_id=data["schedule_id"],
                specialist_id=specialist_id,
                comment=comment,
                now=datetime.now(UTC),
            )
        card = data.get("card") or ""
        await state.clear()
        if saved is None:
            await message.answer(self._m.not_found)
            return
        await message.answer(self._m.comment_set)
        await _open_card(
            self._navigator, message, specialist_id=specialist_id, card_back=card
        )

    # --- comment a single occurrence -----------------------------------------

    async def start_comment(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        head, back = _split_back(callback.data)
        slot_id, origin_date = _parse_slot_date(head)
        slot = await self._load_slot(slot_id, specialist_id)
        if slot is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await state.clear()
        await state.update_data(
            flow="occ_comment",
            slot_id=slot_id,
            origin_date=origin_date.isoformat(),
            card=_with_back(f"recur:occ:{slot_id}:{origin_date.isoformat()}", back),
        )
        await state.set_state(Recurring.occ_comment)
        await _callback_message(callback).edit_text(
            self._m.ask_occ_comment, reply_markup=_recur_cancel_keyboard()
        )
        await callback.answer()

    async def apply_occ_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        comment = (message.text or "").strip() or None
        origin_date = date.fromisoformat(data["origin_date"])
        async with self._session_factory() as session:
            saved = await set_occurrence_comment(
                SqlAlchemyRecurringSlotRepo(session),
                SqlAlchemyRecurringSlotOverrideRepo(session),
                slot_id=data["slot_id"],
                specialist_id=specialist_id,
                original_date=origin_date,
                comment=comment,
                now=datetime.now(UTC),
            )
        card = data.get("card") or ""
        await state.clear()
        if saved is None:
            await message.answer(self._m.not_found)
            return
        await message.answer(self._m.comment_set)
        await _open_card(
            self._navigator, message, specialist_id=specialist_id, card_back=card
        )

    async def cancel(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        card = data.get("card") or ""  # the card this flow started from, if any
        await state.clear()
        await _post_action(
            self._navigator,
            _callback_message(callback),
            result_text=self._m.cancelled,
            back=card,
            specialist_id=specialist_id,
            edit=True,
        )
        await callback.answer()


def _build_navigator(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    h: ScheduleHandlers,
    r: RecurringHandlers,
) -> Navigator:
    # The clients router owns the client-side targets; reuse its builders here so an
    # appointment action can re-open a client card without a cross-handler import
    # cycle (clients never imports schedule).
    ch = ClientsHandlers(messages, session_factory)
    return Navigator(
        builders={
            _CB_FEED: h.nav_day,
            "sched:day_view:": h.nav_day,
            "sched:chist:": h.nav_client_history,
            "sched:card:": h.nav_appt_card,
            "recur:sched:": r.nav_schedule_card,
            "recur:occ:": r.nav_meeting_card,
            "clients:card:": ch.nav_card,
            "clients:active:": ch.nav_active,
            "clients:arch:": ch.nav_archive,
        },
        # Unknown/empty target → today's schedule (preserves the old fallback).
        fallback=h.nav_day,
    )


def _register_recurring(router: Router, r: RecurringHandlers) -> None:
    router.message.register(r.apply_custom_time, Recurring.custom_time)
    router.message.register(r.apply_schedule_comment, Recurring.comment)
    router.message.register(r.apply_sched_comment, Recurring.sched_comment)
    router.message.register(r.apply_occ_comment, Recurring.occ_comment)

    # creation wizard (entered from the appointment flow's "make regular")
    router.callback_query.register(r.add_day, F.data == "recur:add")
    router.callback_query.register(r.done_adding, F.data == "recur:done")
    router.callback_query.register(r.skip_schedule_comment, F.data == "recur:cskipc")
    # shared weekday / time pickers (dispatch on FSM flow)
    router.callback_query.register(r.pick_weekday, F.data.startswith("recur:wd:"))
    router.callback_query.register(r.pick_slot, F.data.startswith("recur:tslot:"))
    router.callback_query.register(r.ask_custom_time, F.data == "recur:tother")
    router.callback_query.register(r.cancel, F.data == "recur:cancel")
    # edit the series' shared comment from the schedule card
    router.callback_query.register(
        r.start_sched_comment, F.data.startswith("recur:schedcmt:")
    )
    # two-level cards
    router.callback_query.register(r.show_schedule, F.data.startswith("recur:sched:"))
    router.callback_query.register(r.show_meeting, F.data.startswith("recur:occ:"))
    # configure: slot list, per-slot actions, add a day
    router.callback_query.register(r.show_config, F.data.startswith("recur:cfg:"))
    router.callback_query.register(r.start_cfg_add, F.data.startswith("recur:cfgadd:"))
    router.callback_query.register(
        r.show_slot_actions, F.data.startswith("recur:slot:")
    )
    router.callback_query.register(
        r.start_slot_time, F.data.startswith("recur:slottime:")
    )
    router.callback_query.register(
        r.start_slot_day, F.data.startswith("recur:slotday:")
    )
    router.callback_query.register(r.delete_slot, F.data.startswith("recur:slotdel:"))
    # stop the whole schedule
    router.callback_query.register(r.confirm_stop, F.data.startswith("recur:stopask:"))
    router.callback_query.register(r.do_stop, F.data.startswith("recur:stop:"))
    # single-occurrence actions: move / skip / comment
    router.callback_query.register(r.start_move, F.data.startswith("recur:occmove:"))
    router.callback_query.register(r.navigate_move, F.data.startswith("recur:cal:"))
    router.callback_query.register(r.pick_move_day, F.data.startswith("recur:day:"))
    router.callback_query.register(
        r.confirm_skip, F.data.startswith("recur:occskipask:")
    )
    router.callback_query.register(r.do_skip, F.data.startswith("recur:occskip:"))
    router.callback_query.register(r.start_comment, F.data.startswith("recur:occcmt:"))


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="schedule")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = ScheduleHandlers(messages, session_factory)
    r = RecurringHandlers(messages, session_factory)
    navigator = _build_navigator(messages, session_factory, h, r)
    h.set_navigator(navigator)
    r.set_navigator(navigator)
    _register_recurring(router, r)

    router.message.register(h.show_feed, F.text == messages.schedule.button)
    router.message.register(h.apply_custom_time, Schedule.custom_time)
    router.message.register(h.apply_comment, Schedule.comment)
    router.message.register(h.apply_edit_comment, Schedule.edit_comment)
    router.message.register(h.apply_notify_custom_time, Schedule.notify_custom_time)

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
    router.callback_query.register(
        h.start_edit_comment, F.data.startswith("sched:cmt:")
    )
    router.callback_query.register(h.notify_skip, F.data == _CB_NOTIFY_NO)
    router.callback_query.register(h.ask_when, F.data == _CB_NOTIFY_WHEN)
    router.callback_query.register(h.notify_now, F.data == _CB_NOTIFY_NOW)
    router.callback_query.register(h.notify_preset, F.data == _CB_NOTIFY_PRESET)
    router.callback_query.register(h.notify_custom, F.data == _CB_NOTIFY_CUSTOM)
    router.callback_query.register(
        h.notify_custom_cancel, F.data == _CB_NOTIFY_CUSTOM_CANCEL
    )
    return router
