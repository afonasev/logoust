from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import ClientsHandlers
from src.bot.handlers.subscriptions import (
    SubscriptionFlow,
    SubscriptionsHandlers,
    render_card,
)
from src.bot.messages import BotMessages
from src.domain.client import Client, ClientStatus
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.clients import NewClient, add_client, archive_client
from src.services.invites import create_invite
from src.services.subscriptions import create_subscription, get_active

_SP = 1


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
    return cb


def _texts(mock: AsyncMock) -> list[str]:
    return [c.args[0] for c in mock.await_args_list]


def _markup(mock: AsyncMock) -> Any:
    call = mock.await_args
    assert call is not None
    return call.kwargs["reply_markup"]


def _callbacks(markup: Any) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _button_texts(markup: Any) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_client(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: ClientStatus = ClientStatus.ACTIVE,
) -> Client:
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
        if status is ClientStatus.ARCHIVED:
            await archive_client(repo, client_id=client.id, specialist_id=_SP)
            client = await repo.get_for_specialist(client.id, _SP)
            assert client is not None
    return client


def _subs(
    messages: BotMessages, factory: async_sessionmaker[AsyncSession]
) -> SubscriptionsHandlers:
    return SubscriptionsHandlers(messages, factory)


async def _active_id(factory: async_sessionmaker[AsyncSession], client_id: int) -> int:
    async with factory() as session:
        sub = await get_active(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client_id,
            specialist_id=_SP,
        )
    assert sub is not None
    assert sub.id is not None
    return sub.id


# --- render -------------------------------------------------------------------


def test_render_card_shows_counts(messages: BotMessages):
    from datetime import UTC, datetime

    from src.domain.subscription import Subscription, SubscriptionStatus

    sub = Subscription(
        id=1,
        client_id=10,
        specialist_id=_SP,
        purchased=8,
        remaining=3,
        status=SubscriptionStatus.ACTIVE,
        created_at=datetime(2026, 6, 6, tzinfo=UTC),
    )
    text = render_card(sub, "Петя", "Asia/Yekaterinburg", messages.subscriptions)
    assert "Петя" in text
    assert "8" in text
    assert "3" in text


# --- create -------------------------------------------------------------------


async def test_start_create_sets_state_and_prompt(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:create:{client.id}")
    state = _state()
    await h.start_create(cb, state, _SP)
    assert state.state == SubscriptionFlow.create_meetings
    assert state.store["client_id"] == client.id
    # Default presets become buttons (4/8/12) carrying the chosen value.
    assert _button_texts(_markup(cb.message.edit_text))[:3] == ["4", "8", "12"]
    assert f"subs:createval:{client.id}:8" in _callbacks(_markup(cb.message.edit_text))


async def test_create_preset_creates_subscription(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:createval:{client.id}:8")
    state = _state(state=SubscriptionFlow.create_meetings)
    await h.create_preset(cb, state, _SP)
    assert state.state is None
    async with session_factory() as session:
        sub = await get_active(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
        )
    assert sub is not None
    assert sub.purchased == 8


async def test_create_value_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    h = _subs(messages, session_factory)
    msg = _fake_message("4")
    state = _state({"client_id": client.id}, SubscriptionFlow.create_meetings)
    await h.create_value(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.created
    async with session_factory() as session:
        sub = await get_active(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
        )
    assert sub is not None
    assert sub.purchased == 4


async def test_create_value_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _subs(messages, session_factory)
    msg = _fake_message("0")
    state = _state({"client_id": client.id}, SubscriptionFlow.create_meetings)
    await h.create_value(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.bad_meetings
    assert state.state == SubscriptionFlow.create_meetings


async def test_create_preset_opens_existing_when_active(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        await create_subscription(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
            meetings=5,
        )
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:createval:{client.id}:8")
    await h.create_preset(cb, _state(state=SubscriptionFlow.create_meetings), _SP)
    # Existing active subscription's card is shown (remaining 5), not a new one.
    assert "5" in _texts(cb.message.edit_text)[0]


async def test_cancel_create_clears_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:cancel:7")
    state = _state(state=SubscriptionFlow.create_meetings)
    await h.cancel_create(cb, state)
    assert state.state is None
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.cancelled
    assert _callbacks(_markup(cb.message.edit_text)) == ["clients:card:7"]


# --- card ---------------------------------------------------------------------


async def test_show_card_renders(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        await create_subscription(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
            meetings=8,
        )
    sid = await _active_id(session_factory, client.id)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:card:{sid}")
    await h.show_card(cb, _state(state=SubscriptionFlow.create_meetings), _SP)
    assert "Петя" in _texts(cb.message.edit_text)[0]


async def test_show_card_not_found_alerts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:card:404")
    await h.show_card(cb, _state(), _SP)
    cb.answer.assert_awaited_with(messages.subscriptions.not_found, show_alert=True)


# --- extend -------------------------------------------------------------------


async def _seed_active(
    session_factory: async_sessionmaker[AsyncSession], *, meetings: int = 8
) -> int:
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        await create_subscription(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
            meetings=meetings,
        )
    return await _active_id(session_factory, client.id)


async def test_start_extend_sets_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:extend:{sid}")
    state = _state()
    await h.start_extend(cb, state, _SP)
    assert state.state == SubscriptionFlow.extend_meetings
    assert state.store["subscription_id"] == sid
    # Preset buttons carry "subs:extendval:<sid>:<n>".
    assert f"subs:extendval:{sid}:8" in _callbacks(_markup(cb.message.edit_text))


async def test_extend_preset(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory, meetings=8)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:extendval:{sid}:8")
    await h.extend_preset(cb, _state(state=SubscriptionFlow.extend_meetings), _SP)
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sid, _SP)
    assert sub is not None
    assert sub.purchased == 16
    assert sub.remaining == 16


async def test_extend_preset_not_found_alerts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:extendval:404:8")
    await h.extend_preset(cb, _state(state=SubscriptionFlow.extend_meetings), _SP)
    cb.answer.assert_awaited_with(messages.subscriptions.not_found, show_alert=True)


async def test_extend_value_valid(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory, meetings=8)
    h = _subs(messages, session_factory)
    msg = _fake_message("2")
    state = _state({"subscription_id": sid}, SubscriptionFlow.extend_meetings)
    await h.extend_value(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.extended
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sid, _SP)
    assert sub is not None
    assert sub.purchased == 10


async def test_extend_value_invalid_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    msg = _fake_message("abc")
    state = _state({"subscription_id": sid}, SubscriptionFlow.extend_meetings)
    await h.extend_value(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.bad_meetings
    assert state.state == SubscriptionFlow.extend_meetings


async def test_extend_value_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    msg = _fake_message("2")
    state = _state({"subscription_id": 404}, SubscriptionFlow.extend_meetings)
    await h.extend_value(msg, state, _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.not_found


# --- decrement ----------------------------------------------------------------


async def test_decrement_success(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory, meetings=5)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:dec:{sid}")
    await h.decrement(cb, _SP)
    cb.answer.assert_awaited_with(messages.subscriptions.decremented)
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sid, _SP)
    assert sub is not None
    assert sub.remaining == 4


async def test_decrement_at_zero_alerts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory, meetings=1)
    h = _subs(messages, session_factory)
    # Spend the only meeting first.
    await h.decrement(_fake_callback(f"subs:dec:{sid}"), _SP)
    cb = _fake_callback(f"subs:dec:{sid}")
    await h.decrement(cb, _SP)
    cb.answer.assert_awaited_with(
        messages.subscriptions.nothing_to_decrement, show_alert=True
    )


async def test_decrement_not_found_alerts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:dec:404")
    await h.decrement(cb, _SP)
    cb.answer.assert_awaited_with(messages.subscriptions.not_found, show_alert=True)


# --- close --------------------------------------------------------------------


async def test_ask_close_shows_confirm(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:closeask:{sid}")
    await h.ask_close(cb)
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.close_confirm
    assert f"subs:close:{sid}" in _callbacks(_markup(cb.message.edit_text))


async def test_close_shows_closed_and_back(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback(f"subs:close:{sid}")
    await h.close(cb, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.closed
    assert any(
        c.startswith("clients:card:") for c in _callbacks(_markup(cb.message.edit_text))
    )


async def test_close_not_found_alerts(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:close:404")
    await h.close(cb, _SP)
    cb.answer.assert_awaited_with(messages.subscriptions.not_found, show_alert=True)


# --- lists: active & closed ---------------------------------------------------


async def test_show_list_empty(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    msg = _fake_message()
    await h.show_list(msg, _state(state="x"), _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.list_active_empty


async def test_show_list_with_active(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory, meetings=5)
    h = _subs(messages, session_factory)
    msg = _fake_message()
    await h.show_list(msg, _state(), _SP)
    assert _texts(msg.answer)[0] == messages.subscriptions.list_active_title
    markup = _markup(msg.answer)
    callbacks = _callbacks(markup)
    assert f"subs:card:{sid}" in callbacks
    assert "subs:closed:0" in callbacks
    # Row label carries the child name and remaining.
    assert any("Петя" in t and "5" in t for t in _button_texts(markup))


async def test_show_active_edits(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:active:0")
    await h.show_active(cb, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.list_active_title


async def test_show_closed_lists_closed(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    # Close it so it lands in history.
    h = _subs(messages, session_factory)
    await h.close(_fake_callback(f"subs:close:{sid}"), _SP)
    cb = _fake_callback("subs:closed:0")
    await h.show_closed(cb, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.list_closed_title
    callbacks = _callbacks(_markup(cb.message.edit_text))
    assert f"subs:card:{sid}" in callbacks
    assert "subs:active:0" in callbacks


async def test_show_closed_empty(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _subs(messages, session_factory)
    cb = _fake_callback("subs:closed:0")
    await h.show_closed(cb, _SP)
    assert _texts(cb.message.edit_text)[0] == messages.subscriptions.list_closed_empty


async def test_closed_card_is_readonly(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sid = await _seed_active(session_factory)
    h = _subs(messages, session_factory)
    await h.close(_fake_callback(f"subs:close:{sid}"), _SP)
    cb = _fake_callback(f"subs:card:{sid}")
    await h.show_card(cb, _state(), _SP)
    text = _texts(cb.message.edit_text)[0]
    assert messages.subscriptions.closed_note in text
    callbacks = _callbacks(_markup(cb.message.edit_text))
    # Only "back to client" — no action buttons on a closed subscription.
    assert all(not c.startswith("subs:") for c in callbacks)
    assert any(c.startswith("clients:card:") for c in callbacks)


async def _seed_many_active(
    session_factory: async_sessionmaker[AsyncSession], count: int
) -> None:
    await _seed_specialist(session_factory)
    async with session_factory() as session:
        clients_repo = SqlAlchemyClientsRepo(session)
        subs_repo = SqlAlchemySubscriptionsRepo(session)
        for i in range(count):
            client = await add_client(
                clients_repo,
                NewClient(
                    specialist_id=_SP,
                    child_name=f"Клиент {i}",
                    contact_name="Мама",
                    contact_phone="89161234567",
                ),
            )
            assert client.id is not None
            await create_subscription(
                subs_repo,
                client_id=client.id,
                specialist_id=_SP,
                meetings=8,
            )


async def test_active_list_paginates(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_many_active(session_factory, 9)  # > _PAGE_SIZE (8)
    h = _subs(messages, session_factory)
    msg = _fake_message()
    await h.show_list(msg, _state(), _SP)
    assert "subs:active:1" in _callbacks(_markup(msg.answer))  # next-page nav
    cb = _fake_callback("subs:active:1")
    await h.show_active(cb, _SP)
    assert "subs:active:0" in _callbacks(_markup(cb.message.edit_text))  # prev-page nav


async def test_closed_list_paginates(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_many_active(session_factory, 9)
    h = _subs(messages, session_factory)
    # Close them all so they populate the closed list beyond one page.
    async with session_factory() as session:
        subs_repo = SqlAlchemySubscriptionsRepo(session)
        active = await subs_repo.list_active_for_specialist(_SP, limit=100, offset=0)
    for s in active:
        assert s.id is not None
        await h.close(_fake_callback(f"subs:close:{s.id}"), _SP)
    cb = _fake_callback("subs:closed:0")
    await h.show_closed(cb, _SP)
    assert "subs:closed:1" in _callbacks(_markup(cb.message.edit_text))


# --- client card button -------------------------------------------------------


async def test_client_card_shows_create_button(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = ClientsHandlers(messages, session_factory)
    _, keyboard = await h._card_view(client, _SP)  # noqa: SLF001
    assert messages.clients.btn_subscription_create in _button_texts(keyboard)


async def test_client_card_shows_open_button_with_remaining(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        await create_subscription(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client.id,
            specialist_id=_SP,
            meetings=5,
        )
    h = ClientsHandlers(messages, session_factory)
    _, keyboard = await h._card_view(client, _SP)  # noqa: SLF001
    expected = messages.clients.btn_subscription_open.format(remaining=5)
    assert expected in _button_texts(keyboard)


async def test_archived_client_card_hides_subscription_button(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, status=ClientStatus.ARCHIVED)
    h = ClientsHandlers(messages, session_factory)
    _, keyboard = await h._card_view(client, _SP)  # noqa: SLF001
    assert not any(c.startswith("subs:") for c in _callbacks(keyboard))
