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
from src.domain.schedule import utc_to_wall
from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.clients import client_name_map
from src.services.subscriptions import (
    SubscriptionsPage,
    close_subscription,
    create_subscription,
    decrement_meeting,
    extend_subscription,
    get_card,
    list_active_page,
    list_closed_page,
    parse_meetings,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8
_BTN_PREV = "◀"
_BTN_NEXT = "▶"
_DASH = "—"  # fallback when a client name is unexpectedly missing from the map


class SubscriptionFlow(StatesGroup):
    create_meetings = State()
    extend_meetings = State()


def _callback_message(callback: CallbackQuery) -> Message:
    msg = callback.message
    if msg is None:  # pragma: no cover - our callbacks always carry a message
        msg = "callback query has no message"
        raise RuntimeError(msg)
    return cast("Message", msg)


def _parse_id(callback_data: str | None) -> int:
    # callback_data shape: "subs:<action>:<id>"; the id is the last segment.
    return int((callback_data or "").rsplit(":", 1)[1])


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


def _card_keyboard(
    subscription: Subscription, m: SubscriptionsMessages
) -> InlineKeyboardMarkup:
    sid = subscription.id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_decrement, callback_data=f"subs:dec:{sid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=m.btn_extend, callback_data=f"subs:extend:{sid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=m.btn_close, callback_data=f"subs:closeask:{sid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=m.btn_back_client,
                    callback_data=f"clients:card:{subscription.client_id}",
                )
            ],
        ]
    )


def _prompt_keyboard(
    accept_callback: str, cancel_callback: str, default: int, m: SubscriptionsMessages
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=m.btn_default.format(default=default),
                    callback_data=accept_callback,
                )
            ],
            [InlineKeyboardButton(text=m.btn_cancel, callback_data=cancel_callback)],
        ]
    )


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
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
            assert specialist is not None  # noqa: S101 — middleware guarantees it
            client = await SqlAlchemyClientsRepo(session).get_for_specialist(
                subscription.client_id, specialist_id
            )
        child = client.child_name if client is not None else self._m.not_found
        text = render_card(subscription, child, specialist.timezone, self._m)
        # A closed subscription is read-only: no actions, only back to the client.
        if subscription.status is SubscriptionStatus.CLOSED:
            text = f"{text}\n{self._m.closed_note}"
            return text, _back_to_client_keyboard(subscription.client_id, self._m)
        return text, _card_keyboard(subscription, self._m)

    async def _default(self, specialist_id: int) -> int:
        async with self._session_factory() as session:
            specialist = await SqlAlchemySpecialistsRepo(session).get(specialist_id)
        assert specialist is not None  # noqa: S101 — middleware guarantees it
        return specialist.subscription_default

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
        default = await self._default(specialist_id)
        await _callback_message(callback).edit_text(
            self._m.create_prompt.format(default=default),
            reply_markup=_prompt_keyboard(
                f"subs:createdef:{client_id}",
                f"subs:cancel:{client_id}",
                default,
                self._m,
            ),
        )
        await callback.answer()

    async def create_default(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        client_id = _parse_id(callback.data)
        meetings = await self._default(specialist_id)
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
        default = await self._default(specialist_id)
        await _callback_message(callback).edit_text(
            self._m.extend_prompt.format(default=default),
            reply_markup=_prompt_keyboard(
                f"subs:extenddef:{sid}", f"subs:card:{sid}", default, self._m
            ),
        )
        await callback.answer()

    async def extend_default(
        self, callback: CallbackQuery, state: FSMContext, specialist_id: int
    ) -> None:
        sid = _parse_id(callback.data)
        meetings = await self._default(specialist_id)
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
            repo = SqlAlchemySubscriptionsRepo(session)
            subscription = await get_card(
                repo, subscription_id=sid, specialist_id=specialist_id
            )
        if subscription is None:
            await callback.answer(self._m.not_found, show_alert=True)
            return
        if subscription.remaining <= 0:
            await callback.answer(self._m.nothing_to_decrement, show_alert=True)
            return
        async with self._session_factory() as session:
            subscription = await decrement_meeting(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sid,
                specialist_id=specialist_id,
            )
        assert subscription is not None  # noqa: S101 — existed a moment ago
        text, keyboard = await self._card_view(subscription, specialist_id)
        await _callback_message(callback).edit_text(text, reply_markup=keyboard)
        await callback.answer(self._m.decremented)

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

    router.callback_query.register(h.show_active, F.data.startswith("subs:active:"))
    router.callback_query.register(h.show_closed, F.data.startswith("subs:closed:"))
    router.callback_query.register(h.show_card, F.data.startswith("subs:card:"))
    router.callback_query.register(h.start_create, F.data.startswith("subs:create:"))
    router.callback_query.register(
        h.create_default, F.data.startswith("subs:createdef:")
    )
    router.callback_query.register(h.cancel_create, F.data.startswith("subs:cancel:"))
    router.callback_query.register(h.start_extend, F.data.startswith("subs:extend:"))
    router.callback_query.register(
        h.extend_default, F.data.startswith("subs:extenddef:")
    )
    router.callback_query.register(h.decrement, F.data.startswith("subs:dec:"))
    router.callback_query.register(h.ask_close, F.data.startswith("subs:closeask:"))
    router.callback_query.register(h.close, F.data.startswith("subs:close:"))
    return router
