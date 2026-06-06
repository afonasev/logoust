from datetime import UTC, datetime
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
from src.bot.messages import BotMessages, SubscriptionsMessages
from src.domain.deduction import SubscriptionDeduction
from src.domain.schedule import utc_to_wall
from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import (
    SqlAlchemySubscriptionDeductionsRepo,
    SqlAlchemySubscriptionsRepo,
)
from src.services.clients import client_name_map
from src.services.subscriptions import (
    SubscriptionsPage,
    cancel_deduction,
    close_subscription,
    create_subscription,
    decrement_meeting,
    extend_subscription,
    get_card,
    get_deduction,
    list_active_page,
    list_closed_page,
    list_deductions,
    parse_meetings,
    presets_list,
    set_deduction_comment,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8
_BTN_PREV = "◀"
_BTN_NEXT = "▶"
_DASH = "—"  # fallback when a client name is unexpectedly missing from the map


class SubscriptionFlow(StatesGroup):
    create_meetings = State()
    extend_meetings = State()
    closing_comment = State()


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _parse_id(callback_data: str | None) -> int:
    # callback_data shape: "subs:<action>:<id>"; the id is the last segment.
    return int((callback_data or "").rsplit(":", 1)[1])


def _parse_id_value(callback_data: str | None) -> tuple[int, int]:
    # callback_data shape: "subs:<action>:<id>:<value>" (preset pick).
    parts = (callback_data or "").split(":")
    return int(parts[2]), int(parts[3])


def render_card(
    subscription: Subscription, child_name: str, tz: str, m: SubscriptionsMessages
) -> str:
    created = utc_to_wall(subscription.created_at, tz).strftime("%d.%m.%Y")
    return m.card.format(
        child=child_name,
        created=created,
        purchased=subscription.purchased,
        remaining=subscription.remaining,
    )


def _journal_rows(
    deductions: list[SubscriptionDeduction], tz: str, m: SubscriptionsMessages
) -> list[list[InlineKeyboardButton]]:
    """One button per non-cancelled deduction (tap → its screen). Empty when none."""
    rows: list[list[InlineKeyboardButton]] = []
    for d in deductions:
        created = utc_to_wall(d.created_at, tz).strftime("%d.%m.%Y")
        if d.appointment_starts_at is not None:
            appt = utc_to_wall(d.appointment_starts_at, tz).strftime("%d.%m %H:%M")
            label = m.journal_row_auto.format(date=created, appt=appt)
        else:
            label = m.journal_row_manual.format(date=created)
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"subs:ded:{d.id}")]
        )
    return rows


def _card_keyboard(
    subscription: Subscription,
    deductions: list[SubscriptionDeduction],
    tz: str,
    m: SubscriptionsMessages,
) -> InlineKeyboardMarkup:
    sid = subscription.id
    rows = [
        [InlineKeyboardButton(text=m.btn_decrement, callback_data=f"subs:dec:{sid}")],
        [InlineKeyboardButton(text=m.btn_extend, callback_data=f"subs:extend:{sid}")],
        [InlineKeyboardButton(text=m.btn_close, callback_data=f"subs:closeask:{sid}")],
    ]
    rows.extend(_journal_rows(deductions, tz, m))
    rows.append(
        [
            InlineKeyboardButton(
                text=m.btn_back_client,
                callback_data=f"clients:card:{subscription.client_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _closed_card_keyboard(
    subscription: Subscription,
    deductions: list[SubscriptionDeduction],
    tz: str,
    m: SubscriptionsMessages,
) -> InlineKeyboardMarkup:
    # Closed subscription: journal is read-only (still tappable), only back to client.
    rows = _journal_rows(deductions, tz, m)
    rows.append(
        [
            InlineKeyboardButton(
                text=m.btn_back_client,
                callback_data=f"clients:card:{subscription.client_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prompt_keyboard(
    presets: list[int],
    value_prefix: str,
    cancel_callback: str,
    m: SubscriptionsMessages,
) -> InlineKeyboardMarkup:
    # One button per preset variant (chunked four-per-row so a long list wraps),
    # then Cancel. Typing a custom number still works via the FSM message handler.
    buttons = [
        InlineKeyboardButton(text=str(n), callback_data=f"{value_prefix}{n}")
        for n in presets
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append(
        [InlineKeyboardButton(text=m.btn_cancel, callback_data=cancel_callback)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _close_confirm_keyboard(sid: int, m: SubscriptionsMessages) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_confirm_close, callback_data=f"subs:close:{sid}"
                ),
                InlineKeyboardButton(
                    text=m.btn_cancel, callback_data=f"subs:card:{sid}"
                ),
            ]
        ]
    )


def _back_to_client_keyboard(
    client_id: int, m: SubscriptionsMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_back_client, callback_data=f"clients:card:{client_id}"
                )
            ]
        ]
    )


def _nav_row(page: SubscriptionsPage, prefix: str) -> list[InlineKeyboardButton]:
    nav: list[InlineKeyboardButton] = []
    if page.has_prev:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_PREV, callback_data=f"{prefix}{page.page - 1}"
            )
        )
    if page.has_next:
        nav.append(
            InlineKeyboardButton(
                text=_BTN_NEXT, callback_data=f"{prefix}{page.page + 1}"
            )
        )
    return nav


def _active_list_keyboard(
    page: SubscriptionsPage, names: dict[int, str], m: SubscriptionsMessages
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=m.list_row_active.format(
                    child=names.get(s.client_id, _DASH),
                    remaining=s.remaining,
                ),
                callback_data=f"subs:card:{s.id}",
            )
        ]
        for s in page.items
    ]
    nav = _nav_row(page, "subs:active:")
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text=m.btn_closed, callback_data="subs:closed:0")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _closed_list_keyboard(
    page: SubscriptionsPage,
    names: dict[int, str],
    tz: str,
    m: SubscriptionsMessages,
) -> InlineKeyboardMarkup:
    rows = []
    for s in page.items:
        closed = (
            utc_to_wall(s.closed_at, tz).strftime("%d.%m.%Y")
            if s.closed_at is not None
            else ""
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=m.list_row_closed.format(
                        child=names.get(s.client_id, _DASH), closed=closed
                    ),
                    callback_data=f"subs:card:{s.id}",
                )
            ]
        )
    nav = _nav_row(page, "subs:closed:")
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton(text=m.btn_active, callback_data="subs:active:0")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


class SubscriptionsHandlers:
    def __init__(
        self,
        messages: BotMessages,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._m = messages.subscriptions
        self._session_factory = session_factory

    async def _card_view(
        self, subscription: Subscription, specialist_id: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        assert subscription.id is not None  # noqa: S101 — persisted subscription
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                subscription.client_id, specialist_id
            )
            deductions = await list_deductions(
                SqlAlchemySubscriptionDeductionsRepo(session),
                subscription_id=subscription.id,
            )
        child = client.child_name if client is not None else self._m.not_found
        tz = specialist.timezone
        text = render_card(subscription, child, tz, self._m)
        # A closed subscription is read-only: no actions, journal stays viewable.
        if subscription.status is SubscriptionStatus.CLOSED:
            text = f"{text}\n{self._m.closed_note}"
            return text, _closed_card_keyboard(subscription, deductions, tz, self._m)
        return text, _card_keyboard(subscription, deductions, tz, self._m)

    async def _presets(self, specialist_id: int) -> list[int]:
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
        assert specialist is not None  # noqa: S101 — middleware guarantees it
        return presets_list(specialist.subscription_presets)

    # --- lists: active & closed -----------------------------------------------

    async def _active_view(
        self, specialist_id: int, page_num: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            page = await list_active_page(
                SqlAlchemySubscriptionsRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_PAGE_SIZE,
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
        if page_num == 0 and not page.items:
            text = self._m.list_active_empty
        else:
            text = self._m.list_active_title
        return text, _active_list_keyboard(page, names, self._m)

    async def _closed_view(
        self, specialist_id: int, page_num: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            page = await list_closed_page(
                SqlAlchemySubscriptionsRepo(session),
                specialist_id=specialist_id,
                page=page_num,
                page_size=_PAGE_SIZE,
            )
            names = await client_name_map(
                SqlAlchemyClientsRepo(session), specialist_id=specialist_id
            )
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
        if page_num == 0 and not page.items:
            text = self._m.list_closed_empty
        else:
            text = self._m.list_closed_title
        return text, _closed_list_keyboard(page, names, specialist.timezone, self._m)

    async def show_list(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        # Reply-кнопка «Абонементы» — открывает активные и гасит любой визард.
        await state.clear()
        text, keyboard = await self._active_view(specialist_id, 0)
        await message.answer(text, reply_markup=keyboard)

    async def show_active(self, callback: CallbackQuery, specialist_id: int) -> None:
        text, keyboard = await self._active_view(
            specialist_id, _parse_id(callback.data)
        )
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def show_closed(self, callback: CallbackQuery, specialist_id: int) -> None:
        text, keyboard = await self._closed_view(
            specialist_id, _parse_id(callback.data)
        )
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    # --- card -----------------------------------------------------------------

    async def show_card(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        # Also the exit from the extend prompt / close confirmation, so clear FSM.
        await state.clear()
        async with self._session_factory() as session:
            subscription = await get_card(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=_parse_id(callback.data),
                specialist_id=specialist_id,
            )
        if subscription is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    # --- create ---------------------------------------------------------------

    async def start_create(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        client_id = _parse_id(callback.data)
        await state.set_state(SubscriptionFlow.create_meetings)
        await state.update_data(client_id=client_id)
        presets = await self._presets(specialist_id)
        await _callback_message(callback).edit_text(
            self._m.create_prompt,
            reply_markup=_prompt_keyboard(
                presets,
                f"subs:createval:{client_id}:",
                f"subs:cancel:{client_id}",
                self._m,
            ),
        )
        await callback.answer()

    async def create_preset(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        client_id, meetings = _parse_id_value(callback.data)
        await state.clear()
        await self._create_cb(callback, specialist_id, client_id, meetings)

    async def create_value(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        meetings = parse_meetings(message.text or "")
        if meetings is None:
            await message.answer(self._m.bad_meetings)
            return
        data = await state.get_data()
        await state.clear()
        await self._create_msg(message, specialist_id, data["client_id"], meetings)

    async def _create_cb(
        self,
        callback: CallbackQuery,
        specialist_id: int,
        client_id: int,
        meetings: int,
    ) -> None:
        subscription = await self._do_create(specialist_id, client_id, meetings)
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def _create_msg(
        self,
        message: Message,
        specialist_id: int,
        client_id: int,
        meetings: int,
    ) -> None:
        subscription = await self._do_create(specialist_id, client_id, meetings)
        await message.answer(self._m.created)
        text, keyboard = await self._card_view(subscription, specialist_id)
        await message.answer(text, reply_markup=keyboard)

    async def _do_create(
        self, specialist_id: int, client_id: int, meetings: int
    ) -> Subscription:
        async with self._session_factory() as session:
            repo = SqlAlchemySubscriptionsRepo(session)
            subscription = await create_subscription(
                repo,
                client_id=client_id,
                specialist_id=specialist_id,
                meetings=meetings,
            )
            if subscription is None:
                # Invariant guard: an active subscription already exists — open it
                # instead of creating a second one.
                subscription = await repo.get_active(client_id, specialist_id)
        assert subscription is not None  # noqa: S101 — created or pre-existing active
        return subscription

    async def cancel_create(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        client_id = _parse_id(callback.data)
        await _callback_message(callback).edit_text(
            self._m.cancelled,
            reply_markup=_back_to_client_keyboard(client_id, self._m),
        )
        await callback.answer()

    # --- extend ---------------------------------------------------------------

    async def start_extend(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        sid = _parse_id(callback.data)
        await state.set_state(SubscriptionFlow.extend_meetings)
        await state.update_data(subscription_id=sid)
        presets = await self._presets(specialist_id)
        await _callback_message(callback).edit_text(
            self._m.extend_prompt,
            reply_markup=_prompt_keyboard(
                presets, f"subs:extendval:{sid}:", f"subs:card:{sid}", self._m
            ),
        )
        await callback.answer()

    async def extend_preset(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        sid, meetings = _parse_id_value(callback.data)
        await state.clear()
        await self._extend_cb(callback, specialist_id, sid, meetings)

    async def extend_value(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        meetings = parse_meetings(message.text or "")
        if meetings is None:
            await message.answer(self._m.bad_meetings)
            return
        data = await state.get_data()
        await state.clear()
        subscription = await self._do_extend(
            specialist_id, data["subscription_id"], meetings
        )
        if subscription is None:
            await message.answer(self._m.not_found)
            return
        await message.answer(self._m.extended)
        text, keyboard = await self._card_view(subscription, specialist_id)
        await message.answer(text, reply_markup=keyboard)

    async def _extend_cb(
        self, callback: CallbackQuery, specialist_id: int, sid: int, meetings: int
    ) -> None:
        subscription = await self._do_extend(specialist_id, sid, meetings)
        if subscription is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def _do_extend(
        self, specialist_id: int, sid: int, meetings: int
    ) -> Subscription | None:
        async with self._session_factory() as session:
            return await extend_subscription(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
                meetings=meetings,
            )

    # --- decrement ------------------------------------------------------------

    async def decrement(self, callback: CallbackQuery, specialist_id: int) -> None:
        sid = _parse_id(callback.data)
        async with self._session_factory() as session:
            deduction = await decrement_meeting(
                SqlAlchemySubscriptionDeductionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
                now=datetime.now(UTC),
            )
        if deduction is None:
            # None = nothing to deduct (remaining already 0) or the subscription is
            # gone/closed; tell the specialist which without a stale card refresh.
            await self._answer_decrement_failure(callback, sid, specialist_id)
            return
        async with self._session_factory() as session:
            subscription = await get_card(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
            )
        assert subscription is not None  # noqa: S101 — just decremented it
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer(self._m.decremented)

    async def _answer_decrement_failure(
        self, callback: CallbackQuery, sid: int, specialist_id: int
    ) -> None:
        async with self._session_factory() as session:
            subscription = await get_card(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
            )
        if subscription is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await callback.answer(self._m.nothing_to_decrement, show_alert=True)

    # --- deduction journal screen ---------------------------------------------

    def _deduction_text(self, deduction: SubscriptionDeduction, tz: str) -> str:
        created = utc_to_wall(deduction.created_at, tz).strftime("%d.%m.%Y")
        lines = [self._m.ded_title, self._m.ded_created.format(date=created)]
        if deduction.appointment_starts_at is not None:
            meeting = utc_to_wall(deduction.appointment_starts_at, tz).strftime(
                "%d.%m.%Y %H:%M"
            )
            lines.append(self._m.ded_meeting.format(meeting=meeting))
        else:
            lines.append(self._m.ded_manual)
        if deduction.appointment_comment:
            lines.append(
                self._m.ded_record_comment.format(comment=deduction.appointment_comment)
            )
        if deduction.closing_comment:
            lines.append(
                self._m.ded_closing_comment.format(comment=deduction.closing_comment)
            )
        else:
            lines.append(self._m.ded_closing_empty)
        return "\n".join(lines)

    def _deduction_keyboard(
        self, deduction: SubscriptionDeduction, *, active: bool
    ) -> InlineKeyboardMarkup:
        did = deduction.id
        rows: list[list[InlineKeyboardButton]] = []
        # Actions only on an active subscription; closed → read-only.
        if active:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self._m.btn_ded_comment,
                        callback_data=f"subs:dedcomment:{did}",
                    ),
                    InlineKeyboardButton(
                        text=self._m.btn_ded_cancel,
                        callback_data=f"subs:dedcancel:{did}",
                    ),
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self._m.btn_back_card,
                    callback_data=f"subs:card:{deduction.subscription_id}",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _deduction_view(
        self, deduction: SubscriptionDeduction, specialist_id: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            subscription = await get_card(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=deduction.subscription_id,
                specialist_id=specialist_id,
            )
        active = (
            subscription is not None
            and subscription.status is SubscriptionStatus.ACTIVE
        )
        text = self._deduction_text(deduction, specialist.timezone)
        return text, self._deduction_keyboard(deduction, active=active)

    async def show_deduction(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        await state.clear()
        async with self._session_factory() as session:
            deduction = await get_deduction(
                SqlAlchemySubscriptionDeductionsRepo(session),
                deduction_id=_parse_id(callback.data),
                specialist_id=specialist_id,
            )
        if deduction is None:
            await callback.answer(self._m.ded_not_found, show_alert=True)
            return
        text, keyboard = await self._deduction_view(deduction, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer()

    async def cancel_deduction(
        self, callback: CallbackQuery, specialist_id: int
    ) -> None:
        did = _parse_id(callback.data)
        async with self._session_factory() as session:
            subscription = await cancel_deduction(
                SqlAlchemySubscriptionDeductionsRepo(session),
                deduction_id=did,
                specialist_id=specialist_id,
                now=datetime.now(UTC),
            )
        if subscription is None:
            await callback.answer(self._m.ded_not_found, show_alert=True)
            return
        # The row is now hidden from the journal — return to the refreshed card.
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer(self._m.ded_cancelled)

    async def start_deduction_comment(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        did = _parse_id(callback.data)
        await state.set_state(SubscriptionFlow.closing_comment)
        await state.update_data(deduction_id=did)
        await _callback_message(callback).edit_text(
            self._m.ask_closing_comment,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=self._m.btn_cancel,
                            callback_data=f"subs:ded:{did}",
                        )
                    ]
                ]
            ),
        )
        await callback.answer()

    async def apply_deduction_comment(
        self, message: Message, state: FSMContext, specialist_id: int
    ) -> None:
        data = await state.get_data()
        await state.clear()
        comment = (message.text or "").strip() or None
        async with self._session_factory() as session:
            deduction = await set_deduction_comment(
                SqlAlchemySubscriptionDeductionsRepo(session),
                deduction_id=data["deduction_id"],
                specialist_id=specialist_id,
                comment=comment,
            )
        if deduction is None:
            await message.answer(self._m.ded_not_found)
            return
        await message.answer(self._m.closing_comment_set)
        text, keyboard = await self._deduction_view(deduction, specialist_id)
        await message.answer(text, reply_markup=keyboard)

    # --- close ----------------------------------------------------------------

    async def ask_close(self, callback: CallbackQuery) -> None:
        sid = _parse_id(callback.data)
        await _callback_message(callback).edit_text(
            self._m.close_confirm, reply_markup=_close_confirm_keyboard(sid, self._m)
        )
        await callback.answer()

    async def close(self, callback: CallbackQuery, specialist_id: int) -> None:
        sid = _parse_id(callback.data)
        async with self._session_factory() as session:
            subscription = await close_subscription(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
            )
        if subscription is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        await _callback_message(callback).edit_text(
            self._m.closed,
            reply_markup=_back_to_client_keyboard(subscription.client_id, self._m),
        )
        await callback.answer()


def build_router(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
) -> Router:
    router = Router(name="subscriptions")
    router.message.middleware(SpecialistMiddleware(session_factory))
    router.callback_query.middleware(SpecialistMiddleware(session_factory))

    h = SubscriptionsHandlers(messages, session_factory)

    # Reply button is registered before the FSM message handlers so it escapes any
    # active create/extend wizard (mirrors the clients router).
    router.message.register(h.show_list, F.text == messages.subscriptions.button)
    router.message.register(h.create_value, SubscriptionFlow.create_meetings)
    router.message.register(h.extend_value, SubscriptionFlow.extend_meetings)
    router.message.register(h.apply_deduction_comment, SubscriptionFlow.closing_comment)

    router.callback_query.register(h.show_active, F.data.startswith("subs:active:"))
    router.callback_query.register(h.show_closed, F.data.startswith("subs:closed:"))
    router.callback_query.register(h.show_card, F.data.startswith("subs:card:"))
    router.callback_query.register(h.start_create, F.data.startswith("subs:create:"))
    router.callback_query.register(
        h.create_preset, F.data.startswith("subs:createval:")
    )
    router.callback_query.register(h.cancel_create, F.data.startswith("subs:cancel:"))
    router.callback_query.register(h.start_extend, F.data.startswith("subs:extend:"))
    router.callback_query.register(
        h.extend_preset, F.data.startswith("subs:extendval:")
    )
    router.callback_query.register(h.decrement, F.data.startswith("subs:dec:"))
    router.callback_query.register(h.ask_close, F.data.startswith("subs:closeask:"))
    router.callback_query.register(h.close, F.data.startswith("subs:close:"))
    # Deduction-journal screen. Registered after subs:dec: — the colons in the
    # prefixes keep "subs:ded:", "subs:dedcancel:", "subs:dedcomment:" disjoint.
    router.callback_query.register(
        h.cancel_deduction, F.data.startswith("subs:dedcancel:")
    )
    router.callback_query.register(
        h.start_deduction_comment, F.data.startswith("subs:dedcomment:")
    )
    router.callback_query.register(h.show_deduction, F.data.startswith("subs:ded:"))
    return router
