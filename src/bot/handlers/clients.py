from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import logging
from typing import Any, cast

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.deeplink import build_client_start_link
from src.bot.messages import (
    BotMessages,
    ClientsMessages,
    RecurringMessages,
    ReminderMessages,
    ScheduleMessages,
)
from src.domain.appointment import Appointment
from src.domain.client import Client, ClientStatus, ClientValidationError
from src.domain.reminder import ReminderStatus
from src.domain.schedule import format_ru_short, utc_to_wall
from src.domain.subscription import Subscription
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.message_templates_repo import SqlAlchemyMessageTemplatesRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringExceptionsRepo,
    SqlAlchemyRecurringRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.appointments import list_client_future, nearest_future_by_client
from src.services.clients import (
    ClientsPage,
    EditResult,
    NewClient,
    add_client,
    archive_client,
    create_client_invite,
    edit_client_field,
    list_active_page,
    list_archived_page,
    restore_client,
)
from src.services.message_templates import resolve_template
from src.services.recurring import SeriesContext, load_series_context, settle
from src.services.reminder import statuses_for_appointments
from src.services.subscriptions import get_active

logger = logging.getLogger(__name__)

# Both the active home list and the archive are paginated.
_ACTIVE_PAGE_SIZE = 8
_ARCHIVE_PAGE_SIZE = 8

# Field keys must match the editable fields accepted by services.edit_client_field.
_FIELD_LABELS = {
    "child_name": "Имя ребёнка",
    "contact_name": "Имя контакта",
    "contact_phone": "Телефон",
    "contact_telegram": "Telegram",
    "extra_contacts": "Доп. контакты",
    "note": "Заметка",
}

_BTN_ADD = "➕ Добавить"  # noqa: RUF001
_BTN_ARCHIVED = "🗄 Архив"
_BTN_EDIT = "✏️ Изменить"
_BTN_ARCHIVE = "📦 В архив"  # noqa: RUF001
_BTN_ARCHIVE_YES = "📦 Да, в архив"
_BTN_RESTORE = "↩️ Вернуть"
_BTN_BACK = "⬅️ Назад"
_BTN_MENU = "⬅️ Меню"
_BTN_SKIP = "Пропустить"
_BTN_CANCEL = "Отмена"
_BTN_PREV = "◀"
_BTN_NEXT = "▶"

_CB_MENU = "clients:menu"
_CB_ADD = "clients:add"
_CB_SKIP = "clients:skip"
_CB_CANCEL = "clients:cancel"


class AddClient(StatesGroup):
    child_name = State()
    contact_name = State()
    contact_phone = State()
    contact_telegram = State()


class EditClient(StatesGroup):
    waiting_value = State()


def build_main_keyboard(messages: BotMessages) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура специалиста: клиенты, расписание, окна, абонементы,
    аудит, настройки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=messages.clients.button),
                KeyboardButton(text=messages.schedule.button),
                KeyboardButton(text=messages.windows.button),
            ],
            [
                KeyboardButton(text=messages.subscriptions.button),
                KeyboardButton(text=messages.audit.button),
                KeyboardButton(text=messages.settings.button),
            ],
        ],
        resize_keyboard=True,
    )


def _appt_label(  # noqa: PLR0913
    appt: Appointment,
    tz: str,
    sm: ScheduleMessages,
    *,
    recur_mark: str,
    with_comment: bool = True,
    status_prefix: str = "",
) -> str:
    wall = utc_to_wall(appt.starts_at, tz)
    comment = (
        sm.comment_suffix.format(comment=appt.comment)
        if with_comment and appt.comment
        else ""
    )
    # A plain recurring occurrence shows 🔁; everything else (one-off, moved) 📅.
    # `status_prefix` (✅/❌) leads when the client answered the reminder.
    prefix = recur_mark if appt.recurring_mark else "📅"
    return (
        f"{status_prefix}{prefix} {format_ru_short(wall.date())} {wall:%H:%M}{comment}"
    )


def _client_row(  # noqa: PLR0913
    client: Client,
    nearest: dict[int, Appointment],
    tz: str,
    sm: ScheduleMessages,
    *,
    page: int,
    recur_mark: str,
    rem: ReminderMessages,
    statuses: dict[tuple[int, datetime], ReminderStatus],
) -> list[InlineKeyboardButton]:
    # Two columns: the client (→ card) and its nearest appointment (→ that
    # appointment's card, to reschedule), or a "create appointment" button if none.
    # Both carry the active-list page as the back target. No comment on the appt
    # button here — it stays compact in the list.
    back = f"clients:active:{page}"
    client_btn = InlineKeyboardButton(
        text=client.child_name, callback_data=f"clients:card:{client.id}~{back}"
    )
    appt = nearest.get(client.id) if client.id is not None else None
    if appt is None:
        second = InlineKeyboardButton(
            text=sm.btn_add, callback_data=f"sched:new:{client.id}"
        )
    else:
        status_prefix = rem.status_mark(statuses.get((appt.client_id, appt.starts_at)))
        second = InlineKeyboardButton(
            text=_appt_label(
                appt,
                tz,
                sm,
                recur_mark=recur_mark,
                with_comment=False,
                status_prefix=status_prefix,
            ),
            callback_data=_appt_callback(appt, back),
        )
    return [client_btn, second]


def _active_keyboard(  # noqa: PLR0913
    page: ClientsPage,
    nearest: dict[int, Appointment],
    tz: str,
    sm: ScheduleMessages,
    recur_mark: str,
    *,
    rem: ReminderMessages,
    statuses: dict[tuple[int, datetime], ReminderStatus],
) -> InlineKeyboardMarkup:
    rows = [
        _client_row(
            c,
            nearest,
            tz,
            sm,
            page=page.page,
            recur_mark=recur_mark,
            rem=rem,
            statuses=statuses,
        )
        for c in page.clients
    ]
    nav = []
    if page.has_prev:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_PREV, callback_data=f"clients:active:{page.page - 1}"
            )
        )
    if page.has_next:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_NEXT, callback_data=f"clients:active:{page.page + 1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text=_BTN_ADD, callback_data=_CB_ADD),
            InlineKeyboardButton(
                text=_BTN_ARCHIVED, callback_data="clients:list:archived"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else ""


def _archive_keyboard(page: ClientsPage) -> InlineKeyboardMarkup:
    back = f"clients:arch:{page.page}"  # back from a card returns to this archive page
    rows = []
    for c in page.clients:
        date = _fmt_date(c.archived_at)
        label = f"{c.child_name} · {date}" if date else c.child_name
        rows.append(
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"clients:card:{c.id}~{back}"
                )
            ]
        )
    nav = []
    if page.has_prev:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_PREV, callback_data=f"clients:arch:{page.page - 1}"
            )
        )
    if page.has_next:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_NEXT, callback_data=f"clients:arch:{page.page + 1}"
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=_BTN_MENU, callback_data=_CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _appt_callback(appt: Appointment, back: str) -> str:
    # A virtual series occurrence (id is None) opens the series card; a real row
    # opens its appointment card.
    if appt.id is None:
        assert appt.series_id is not None  # noqa: S101 — virtual rows carry a series
        assert appt.origin_date is not None  # noqa: S101
        # Carry the origin (e.g. the client card) so the series card returns there.
        return f"recur:card:{appt.series_id}:{appt.origin_date.isoformat()}~{back}"
    return f"sched:card:{appt.id}~{back}"


def _future_button(
    appt: Appointment,
    tz: str,
    m: ScheduleMessages,
    recur_mark: str,
    *,
    status_prefix: str = "",
) -> list[InlineKeyboardButton]:
    # Tapping a future appointment opens its card (reschedule / cancel there);
    # back from there returns to this client's card.
    back = f"clients:card:{appt.client_id}"
    return [
        InlineKeyboardButton(
            text=_appt_label(
                appt, tz, m, recur_mark=recur_mark, status_prefix=status_prefix
            ),
            callback_data=_appt_callback(appt, back),
        )
    ]


def _subscription_button(
    client: Client, subscription: Subscription | None, cm: ClientsMessages
) -> InlineKeyboardButton:
    # No active subscription → create; otherwise jump to its card with the remaining
    # count on the label. Only rendered on active cards (see _card_keyboard).
    if subscription is None:
        return InlineKeyboardButton(
            text=cm.btn_subscription_create, callback_data=f"subs:create:{client.id}"
        )
    return InlineKeyboardButton(
        text=cm.btn_subscription_open.format(remaining=subscription.remaining),
        callback_data=f"subs:card:{subscription.id}",
    )


def _invite_button(client: Client, cm: ClientsMessages) -> InlineKeyboardButton:
    # Label reflects whether the client already bound their Telegram. Re-tapping
    # reuses the same token, so the action is safe to repeat after linking.
    label = (
        cm.invite_button_linked
        if client.telegram_chat_id is not None
        else cm.invite_button
    )
    return InlineKeyboardButton(text=label, callback_data=f"clients:invite:{client.id}")


def _card_keyboard(  # noqa: PLR0913
    client: Client,
    appts: list[Appointment],
    tz: str,
    *,
    m: ScheduleMessages,
    cm: ClientsMessages,
    rm: RecurringMessages,
    rem: ReminderMessages,
    statuses: dict[tuple[int, datetime], ReminderStatus],
    back: str,
    subscription: Subscription | None = None,
) -> InlineKeyboardMarkup:
    if client.status is ClientStatus.ARCHIVED:
        status_btn = InlineKeyboardButton(
            text=_BTN_RESTORE, callback_data=f"clients:restore:{client.id}"
        )
    else:
        # Archiving asks for confirmation first (clients:archiveask:<id>).
        status_btn = InlineKeyboardButton(
            text=_BTN_ARCHIVE, callback_data=f"clients:archiveask:{client.id}"
        )
    rows = [
        _future_button(
            appt,
            tz,
            m,
            rm.mark,
            status_prefix=rem.status_mark(
                statuses.get((appt.client_id, appt.starts_at))
            ),
        )
        for appt in appts
    ]
    rows.append(
        [InlineKeyboardButton(text=m.btn_add, callback_data=f"sched:new:{client.id}")]
    )
    # The subscription button and bot invite are only offered on active cards. A
    # recurring series is created from the normal "записать" flow (a question before
    # the comment), not a separate button here.
    if client.status is ClientStatus.ACTIVE:
        rows.extend(
            (
                [_subscription_button(client, subscription, cm)],
                [_invite_button(client, cm)],
            )
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_BTN_EDIT, callback_data=f"clients:edit:{client.id}"
                ),
                InlineKeyboardButton(
                    text=m.btn_client_history,
                    callback_data=f"sched:chist:{client.id}:0",
                ),
                status_btn,
            ],
            [InlineKeyboardButton(text=_BTN_BACK, callback_data=back)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _archive_confirm_keyboard(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_BTN_ARCHIVE_YES, callback_data=f"clients:archive:{client_id}"
                ),
                InlineKeyboardButton(
                    text=_BTN_CANCEL, callback_data=f"clients:card:{client_id}"
                ),
            ]
        ]
    )


def _back_target(status: ClientStatus) -> str:
    # Active cards return to the paginated active home; archived to the archive.
    if status is ClientStatus.ARCHIVED:
        return "clients:list:archived"
    return "clients:active:0"


def _edit_fields_keyboard(client_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=label, callback_data=f"clients:setfield:{client_id}:{field}"
            )
        ]
        for field, label in _FIELD_LABELS.items()
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=_BTN_BACK, callback_data=f"clients:card:{client_id}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_BTN_SKIP, callback_data=_CB_SKIP),
                InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL),
            ]
        ]
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_BTN_CANCEL, callback_data=_CB_CANCEL)]
        ]
    )


def _or_dash(value: str | None, dash: str) -> str:
    return value or dash


def _telegram_field(client: Client, m: ClientsMessages) -> str:
    # Combine the manually-entered @username (auto-linked by Telegram → tap opens
    # the chat) with a badge when the client is bound to the bot. Two distinct
    # signals: username is how the specialist reaches the parent; the badge means
    # the notification channel is live.
    parts: list[str] = []
    if client.contact_telegram:
        parts.append(f"@{client.contact_telegram}")
    if client.telegram_chat_id is not None:
        parts.append(m.tg_linked_badge)
    return " ".join(parts) if parts else m.dash


def render_card(client: Client, m: ClientsMessages) -> str:
    if client.status is ClientStatus.ARCHIVED:
        status = m.status_archived
    else:
        status = m.status_active
    return m.card.format(
        child=client.child_name,
        contact=client.contact_name,
        phone=_or_dash(client.contact_phone, m.dash),
        telegram=_telegram_field(client, m),
        extra=_or_dash(client.extra_contacts, m.dash),
        note=_or_dash(client.note, m.dash),
        status=status,
    )


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _parse_id(callback_data: str | None) -> int:
    # callback_data shape: "clients:<action>:<id>"; the id is the last segment.
    return int((callback_data or "").rsplit(":", 1)[1])


def _parse_page(callback_data: str | None) -> int:
    # Paginated callbacks (clients:active:<n>, clients:arch:<n>) carry the page in
    # the last segment; entry points without a number (e.g. clients:list:archived)
    # imply page 0.
    last = (callback_data or "").rsplit(":", 1)[-1]
    return int(last) if last.isdigit() else 0


class SpecialistMiddleware(BaseMiddleware):
    """Drop updates from non-onboarded users, inject specialist_id for the rest."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:  # pragma: no cover - messages/callbacks always carry a user
            return None
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).find_by_chat_id(
                user.id
            )
        if specialist is None:
            return None
        assert specialist.id is not None  # noqa: S101 — persisted specialists have id
        # Freeze any passed recurring occurrences into history on interaction
        # (no scheduler). Idempotent + daily-guarded, so it is cheap to repeat.
        async with self._session_factory() as session:
            await settle(
                SqlAlchemyRecurringRepo(session),
                SqlAlchemyRecurringExceptionsRepo(session),
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist.id,
                now=datetime.now(UTC),
                tz=specialist.timezone,
            )
        data["specialist_id"] = specialist.id
        return await handler(event, data)


class ClientsHandlers:  # noqa: PLR0904 — handler aggregator for the clients router
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._messages = messages
        self._m = messages.clients
        self._session_factory = session_factory

    @staticmethod
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

    # --- home: active clients -------------------------------------------------

    async def _active_view(
        self, specialist_id: int, page_num: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            page = await list_active_page(
                SqlAlchemyClientsRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_ACTIVE_PAGE_SIZE,
            )
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            series = await self._series_context(
                session, specialist_id, specialist.timezone
            )
            nearest = await nearest_future_by_client(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                tz=specialist.timezone,
                now=datetime.now(UTC),
                series=series,
            )
            statuses = await statuses_for_appointments(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                appointments=list(nearest.values()),
            )
        if page_num == 0 and not page.clients:
            text = self._m.empty_active
        else:
            text = self._m.list_active_title
        keyboard = _active_keyboard(
            page,
            nearest,
            specialist.timezone,
            self._messages.schedule,
            self._messages.recurring.mark,
            rem=self._messages.reminder,
            statuses=statuses,
        )
        return text, keyboard

    async def show_menu(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        # Pressing the reply button opens the active list and is also the escape
        # hatch out of any active wizard.
        await state.clear()
        text, keyboard = await self._active_view(specialist_id, 0)
        await message.answer(text, reply_markup=keyboard)

    async def open_menu(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        await self._edit_active(callback, specialist_id, 0)

    async def show_active(self, callback: CallbackQuery, specialist_id: int) -> None:
        await self._edit_active(callback, specialist_id, _parse_page(callback.data))

    async def _edit_active(
        self, callback: CallbackQuery, specialist_id: int, page_num: int
    ) -> None:
        text, keyboard = await self._active_view(specialist_id, page_num)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    # --- listing & card -------------------------------------------------------

    async def _archive_view(
        self, specialist_id: int, page_num: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            page = await list_archived_page(
                SqlAlchemyClientsRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_ARCHIVE_PAGE_SIZE,
            )
        if page_num == 0 and not page.clients:
            text = self._m.empty_archived
        else:
            text = self._m.archive_title.format(page=page_num + 1)
        return text, _archive_keyboard(page)

    async def show_archive(self, callback: CallbackQuery, specialist_id: int) -> None:
        text, keyboard = await self._archive_view(
            specialist_id, _parse_page(callback.data)
        )
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def show_card(self, callback: CallbackQuery, specialist_id: int) -> None:
        # "clients:card:<id>[~<back-callback>]" — back returns to the origin.
        head, _, back = (callback.data or "").partition("~")
        await self._open_card(
            callback, specialist_id, int(head.rsplit(":", 1)[1]), back or None
        )

    async def _open_card(
        self,
        callback: CallbackQuery,
        specialist_id: int,
        client_id: int,
        back: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        if client is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._card_view(client, specialist_id, back)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def _card_view(
        self, client: Client, specialist_id: int, back: str | None = None
    ) -> tuple[str, InlineKeyboardMarkup]:
        # Card = general info + the client's upcoming appointments as buttons
        # (tap to reschedule / cancel on the appointment card).
        assert client.id is not None  # noqa: S101 — persisted clients always have id
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            series = await self._series_context(
                session, specialist_id, specialist.timezone
            )
            appts = await list_client_future(
                SqlAlchemyAppointmentsRepo(session),
                specialist_id=specialist_id,
                client_id=client.id,
                tz=specialist.timezone,
                now=datetime.now(UTC),
                series=series,
            )
            statuses = await statuses_for_appointments(
                SqlAlchemyRemindersRepo(session),
                specialist_id=specialist_id,
                appointments=appts,
            )
            # Active subscription drives the card button (create vs open); only
            # relevant for active clients, where the button is shown.
            subscription = (
                await get_active(
                    SqlAlchemySubscriptionsRepo(session),
                    client_id=client.id,
                    specialist_id=specialist_id,
                )
                if client.status is ClientStatus.ACTIVE
                else None
            )
        sm = self._messages.schedule
        # Future appointments are buttons below; no header needed. Note only when
        # there are none. Back defaults to the active list / archive by status.
        text = render_card(client, self._m)
        if not appts:
            text = f"{text}\n\n{sm.client_future_empty}"
        keyboard = _card_keyboard(
            client,
            appts,
            specialist.timezone,
            m=sm,
            cm=self._m,
            rm=self._messages.recurring,
            rem=self._messages.reminder,
            statuses=statuses,
            back=back or _back_target(client.status),
            subscription=subscription,
        )
        return text, keyboard

    async def _card_view_by_id(
        self, specialist_id: int, client_id: int, back: str | None
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                client_id, specialist_id
            )
        # Navigation only ever targets a client we just acted on, so it still exists.
        assert client is not None  # noqa: S101
        return await self._card_view(client, specialist_id, back)

    # --- navigation builders (re-open a target from another router) -----------

    async def nav_card(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        head, _, inner = back.partition("~")
        return await self._card_view_by_id(
            specialist_id, int(head.rsplit(":", 1)[1]), inner or None
        )

    async def nav_active(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        return await self._active_view(specialist_id, _parse_page(back))

    async def nav_archive(
        self, specialist_id: int, back: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        return await self._archive_view(specialist_id, _parse_page(back))

    # --- invite to bot --------------------------------------------------------

    async def send_invite(self, callback: CallbackQuery, specialist_id: int) -> None:
        client_id = _parse_id(callback.data)
        async with self._session_factory() as session:
            client = await create_client_invite(
                SqlAlchemyClientsRepo(session),
                client_id=client_id,
                specialist_id=specialist_id,
            )
            if client is None or client.invite_token is None:
                await callback.answer(self._m.not_found, show_alert=True)
                return
            template = await resolve_template(
                SqlAlchemyMessageTemplatesRepo(session),
                specialist_id=specialist_id,
                key="invite_forward",
                default=self._m.invite_forward,
            )
        link = build_client_start_link(client.invite_token)
        # Separate message so the specialist can forward it to the client as-is.
        await _callback_message(callback).answer(template.format(link=link))
        await callback.answer()

    # --- archive / restore ----------------------------------------------------

    async def ask_archive(self, callback: CallbackQuery) -> None:
        client_id = _parse_id(callback.data)
        await _callback_message(callback).edit_text(
            self._m.archive_confirm, reply_markup=_archive_confirm_keyboard(client_id)
        )
        await callback.answer()

    async def archive(self, callback: CallbackQuery, specialist_id: int) -> None:
        client_id = _parse_id(callback.data)
        async with self._session_factory() as session:
            await archive_client(
                SqlAlchemyClientsRepo(session),
                client_id=client_id,
                specialist_id=specialist_id,
                audit=SqlAlchemyAuditRepo(session),
            )
        await self._open_card(callback, specialist_id, client_id)

    async def restore(self, callback: CallbackQuery, specialist_id: int) -> None:
        client_id = _parse_id(callback.data)
        async with self._session_factory() as session:
            await restore_client(
                SqlAlchemyClientsRepo(session),
                client_id=client_id,
                specialist_id=specialist_id,
                audit=SqlAlchemyAuditRepo(session),
            )
        await self._open_card(callback, specialist_id, client_id)

    # --- add wizard -----------------------------------------------------------

    async def start_add(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(AddClient.child_name)
        await _callback_message(callback).edit_text(
            self._m.ask_child_name, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def add_child_name(self, message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if not value:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.update_data(child_name=value)
        await state.set_state(AddClient.contact_name)
        await message.answer(self._m.ask_contact_name, reply_markup=_cancel_keyboard())

    async def add_contact_name(self, message: Message, state: FSMContext) -> None:
        value = (message.text or "").strip()
        if not value:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.update_data(contact_name=value)
        await state.set_state(AddClient.contact_phone)
        await message.answer(self._m.ask_phone, reply_markup=_skip_keyboard())

    async def add_phone(self, message: Message, state: FSMContext) -> None:
        await state.update_data(contact_phone=(message.text or "").strip())
        await state.set_state(AddClient.contact_telegram)
        await message.answer(self._m.ask_telegram, reply_markup=_skip_keyboard())

    async def skip_phone(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(contact_phone=None)
        await state.set_state(AddClient.contact_telegram)
        await _callback_message(callback).edit_text(
            self._m.ask_telegram, reply_markup=_skip_keyboard()
        )
        await callback.answer()

    async def add_telegram(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        await state.update_data(contact_telegram=(message.text or "").strip())
        await self._create(message, state, specialist_id)

    async def skip_telegram(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.update_data(contact_telegram=None)
        await self._create(_callback_message(callback), state, specialist_id)
        await callback.answer()

    async def _create(
        self, target: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        try:
            async with self._session_factory() as session:
                client = await add_client(
                    SqlAlchemyClientsRepo(session),
                    NewClient(
                        specialist_id=specialist_id,
                        child_name=data["child_name"],
                        contact_name=data["contact_name"],
                        contact_phone=data.get("contact_phone"),
                        contact_telegram=data.get("contact_telegram"),
                    ),
                    audit=SqlAlchemyAuditRepo(session),
                )
        except ClientValidationError:
            # Only NO_CONTACT_CHANNEL is reachable here — required fields were
            # validated per step. Send the specialist back to the phone step.
            await state.set_state(AddClient.contact_phone)
            await target.answer(
                self._m.need_contact_channel, reply_markup=_skip_keyboard()
            )
            return
        await state.clear()
        await target.answer(self._m.added)
        text, keyboard = await self._card_view(client, specialist_id)
        await target.answer(text, reply_markup=keyboard)

    async def cancel(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        _, keyboard = await self._active_view(specialist_id, 0)
        await _callback_message(callback).edit_text(
            self._m.cancelled, reply_markup=keyboard
        )
        await callback.answer()

    # --- edit a field ---------------------------------------------------------

    @staticmethod
    async def start_edit(callback: CallbackQuery) -> None:
        client_id = _parse_id(callback.data)
        await _callback_message(callback).edit_reply_markup(
            reply_markup=_edit_fields_keyboard(client_id)
        )
        await callback.answer()

    async def pick_field(self, callback: CallbackQuery, state: FSMContext) -> None:
        _, _, raw_id, field = (callback.data or "").split(":", 3)
        await state.set_state(EditClient.waiting_value)
        await state.update_data(client_id=int(raw_id), field=field)
        prompt = self._m.edit_prompt.format(label=_FIELD_LABELS[field])
        await _callback_message(callback).edit_text(
            prompt, reply_markup=_cancel_keyboard()
        )
        await callback.answer()

    async def apply_edit(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        async with self._session_factory() as session:
            result = await edit_client_field(
                SqlAlchemyClientsRepo(session),
                client_id=data["client_id"],
                specialist_id=specialist_id,
                field=data["field"],
                value=message.text or "",
            )
        if result is EditResult.EMPTY_REQUIRED:
            await message.answer(
                self._m.empty_required, reply_markup=_cancel_keyboard()
            )
            return
        await state.clear()
        _, keyboard = await self._active_view(specialist_id, 0)
        await message.answer(self._m.updated, reply_markup=keyboard)


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="clients")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = ClientsHandlers(messages, session_factory)

    router.message.register(h.show_menu, F.text == messages.clients.button)
    router.message.register(h.add_child_name, AddClient.child_name)
    router.message.register(h.add_contact_name, AddClient.contact_name)
    router.message.register(h.add_phone, AddClient.contact_phone)
    router.message.register(h.add_telegram, AddClient.contact_telegram)
    router.message.register(h.apply_edit, EditClient.waiting_value)

    router.callback_query.register(h.cancel, F.data == _CB_CANCEL)
    router.callback_query.register(h.open_menu, F.data == _CB_MENU)
    router.callback_query.register(h.start_add, F.data == _CB_ADD)
    router.callback_query.register(
        h.skip_phone, F.data == _CB_SKIP, AddClient.contact_phone
    )
    router.callback_query.register(
        h.skip_telegram, F.data == _CB_SKIP, AddClient.contact_telegram
    )
    router.callback_query.register(h.show_active, F.data.startswith("clients:active:"))
    router.callback_query.register(h.show_archive, F.data == "clients:list:archived")
    router.callback_query.register(h.show_archive, F.data.startswith("clients:arch:"))
    router.callback_query.register(h.show_card, F.data.startswith("clients:card:"))
    router.callback_query.register(h.send_invite, F.data.startswith("clients:invite:"))
    router.callback_query.register(h.start_edit, F.data.startswith("clients:edit:"))
    router.callback_query.register(h.pick_field, F.data.startswith("clients:setfield:"))
    router.callback_query.register(
        h.ask_archive, F.data.startswith("clients:archiveask:")
    )
    router.callback_query.register(h.archive, F.data.startswith("clients:archive:"))
    router.callback_query.register(h.restore, F.data.startswith("clients:restore:"))
    return router
