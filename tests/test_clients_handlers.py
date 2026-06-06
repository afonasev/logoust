from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.clients import (
    AddClient,
    ClientsHandlers,
    EditClient,
    SpecialistMiddleware,
    build_main_keyboard,
    render_card,
)
from src.bot.messages import BotMessages
from src.domain.appointment import Appointment
from src.domain.audit import AuditEvent
from src.domain.client import Client, ClientStatus
from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessageStatus,
)
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import (
    NewClient,
    add_client,
    archive_client,
    list_clients,
)
from src.services.invites import consume_invite, create_invite

if TYPE_CHECKING:
    from aiogram.types import TelegramObject

_SPECIALIST_ID = 1


def now() -> datetime:
    return datetime.now(UTC)


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
    # Returned as Any so it satisfies the FSMContext-typed handler parameters.
    return FakeState(data, state)


def _handlers(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
) -> ClientsHandlers:
    return ClientsHandlers(messages, session_factory)


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


def _first_text(mock: AsyncMock) -> Any:
    call = mock.await_args
    assert call is not None
    return call.args[0]


def _markup(mock: AsyncMock) -> Any:
    call = mock.await_args
    assert call is not None
    return call.kwargs["reply_markup"]


def _button_texts(markup: Any) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


async def _seed_specialist(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The client card now reads specialist schedule settings (timezone) to render
    # inline future appointments, so a specialist row must exist.
    async with session_factory() as session:
        await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_client(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    specialist_id: int = _SPECIALIST_ID,
    child_name: str = "Петя",
    status: ClientStatus = ClientStatus.ACTIVE,
) -> Client:
    async with session_factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=specialist_id,
                child_name=child_name,
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
        assert client.id is not None
        if status is ClientStatus.ARCHIVED:
            await archive_client(repo, client_id=client.id, specialist_id=specialist_id)
            client = await repo.get_for_specialist(client.id, specialist_id)
            assert client is not None
    return client


# --- pure helpers -------------------------------------------------------------


def test_build_main_keyboard_has_clients_button(messages: BotMessages):
    kb = build_main_keyboard(messages)
    assert kb.keyboard[0][0].text == messages.clients.button


def test_render_card_active_uses_dash_for_empty(messages: BotMessages):
    now = datetime.now(UTC)
    client = Client(
        id=1,
        specialist_id=1,
        child_name="Петя",
        contact_name="Мама",
        contact_phone=None,
        contact_telegram=None,
        extra_contacts=None,
        note=None,
        status=ClientStatus.ACTIVE,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    text = render_card(client, messages.clients)
    assert "Петя" in text
    assert messages.clients.dash in text
    assert messages.clients.status_active in text


def test_render_card_archived_shows_values(messages: BotMessages):
    now = datetime.now(UTC)
    client = Client(
        id=1,
        specialist_id=1,
        child_name="Лиза",
        contact_name="Папа",
        contact_phone="+79161234567",
        contact_telegram="masha",
        extra_contacts="бабушка",
        note="любит сказки",
        status=ClientStatus.ARCHIVED,
        archived_at=now,
        created_at=now,
        updated_at=now,
    )
    text = render_card(client, messages.clients)
    assert "+79161234567" in text
    assert "@masha" in text  # username auto-links to the chat
    assert messages.clients.status_archived in text


def test_render_card_telegram_shows_badge_when_linked(messages: BotMessages):
    now_ = now()
    client = Client(
        id=1,
        specialist_id=1,
        child_name="Петя",
        contact_name="Мама",
        contact_phone=None,
        contact_telegram="masha",
        extra_contacts=None,
        note=None,
        status=ClientStatus.ACTIVE,
        archived_at=None,
        created_at=now_,
        updated_at=now_,
        telegram_chat_id=42,
    )
    text = render_card(client, messages.clients)
    assert "@masha" in text
    assert messages.clients.tg_linked_badge in text


def test_render_card_telegram_badge_only_without_username(messages: BotMessages):
    now_ = now()
    client = Client(
        id=1,
        specialist_id=1,
        child_name="Петя",
        contact_name="Мама",
        contact_phone=None,
        contact_telegram=None,
        extra_contacts=None,
        note=None,
        status=ClientStatus.ACTIVE,
        archived_at=None,
        created_at=now_,
        updated_at=now_,
        telegram_chat_id=42,
    )
    text = render_card(client, messages.clients)
    assert messages.clients.tg_linked_badge in text
    assert "@" not in text


# --- menu ---------------------------------------------------------------------


async def test_show_menu_opens_active_list(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    await _seed_client(session_factory, child_name="Аня")
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    state = _state(data={"x": 1})
    await h.show_menu(msg, state, _SPECIALIST_ID)
    assert _first_text(msg.answer) == messages.clients.list_active_title
    markup = _markup(msg.answer)
    assert "Аня" in _button_texts(markup)
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    # No appointment yet → second column is a "create appointment" button.
    assert any(c and c.startswith("sched:new:") for c in cbs)
    assert "clients:add" in cbs  # «Добавить»
    assert "clients:list:archived" in cbs  # «Архив»
    assert state.store == {}


async def test_active_list_shows_nearest_appointment(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Аня")
    assert client.id is not None
    async with session_factory() as session:
        now = datetime.now(UTC)
        await SqlAlchemyAppointmentsRepo(session).add(
            Appointment(
                id=None,
                specialist_id=_SPECIALIST_ID,
                client_id=client.id,
                starts_at=datetime(2030, 1, 15, 9, 0, tzinfo=UTC),  # 14:00 +05
                comment="осмотр",
                created_at=now,
                updated_at=now,
            )
        )
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show_menu(msg, _state(), _SPECIALIST_ID)
    markup = _markup(msg.answer)
    labels = _button_texts(markup)
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    # Two columns: client button + its nearest appointment button (opens the appt).
    # The list button shows date+time but not the comment (kept compact).
    assert "Аня" in labels
    assert any("14:00" in label for label in labels)
    assert not any("осмотр" in label for label in labels)
    assert any(c and c.startswith("sched:card:1~") for c in cbs)  # appt card


async def _seed_reminder_status(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    starts_at: datetime,
    status: ReminderStatus,
) -> None:
    moment = datetime.now(UTC)
    async with session_factory() as session:
        repo = SqlAlchemyRemindersRepo(session)
        reminder = AppointmentReminder(
            id=None,
            specialist_id=_SPECIALIST_ID,
            client_id=client_id,
            starts_at=starts_at,
            series_id=None,
            origin_date=None,
            status=ReminderStatus.PENDING,
            sent_at=moment,
            responded_at=None,
        )
        await repo.insert_pending(reminder)
        assert reminder.id is not None
        await repo.set_status(reminder.id, status, moment)


async def test_active_list_marks_confirmed_nearest(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Аня")
    assert client.id is not None
    starts_at = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
    async with session_factory() as session:
        await SqlAlchemyAppointmentsRepo(session).add(
            Appointment(
                id=None,
                specialist_id=_SPECIALIST_ID,
                client_id=client.id,
                starts_at=starts_at,
                comment=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    await _seed_reminder_status(
        session_factory,
        client_id=client.id,
        starts_at=starts_at,
        status=ReminderStatus.CONFIRMED,
    )
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show_menu(msg, _state(), _SPECIALIST_ID)
    labels = _button_texts(_markup(msg.answer))
    assert any(messages.reminder.confirmed_mark in label for label in labels)


async def test_card_future_marks_declined(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Лиза")
    assert client.id is not None
    starts_at = datetime(2030, 1, 15, 9, 0, tzinfo=UTC)
    async with session_factory() as session:
        await SqlAlchemyAppointmentsRepo(session).add(
            Appointment(
                id=None,
                specialist_id=_SPECIALIST_ID,
                client_id=client.id,
                starts_at=starts_at,
                comment=None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    await _seed_reminder_status(
        session_factory,
        client_id=client.id,
        starts_at=starts_at,
        status=ReminderStatus.DECLINED,
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    labels = [
        b.text for row in _markup(cb.message.edit_text).inline_keyboard for b in row
    ]
    assert any(label.startswith(messages.reminder.declined_mark) for label in labels)


async def test_show_menu_empty(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message()
    await h.show_menu(msg, _state(), _SPECIALIST_ID)
    assert _first_text(msg.answer) == messages.clients.empty_active


async def test_open_menu_edits_to_active_list(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(data={"x": 1}, state="some")
    await h.open_menu(cb, state, _SPECIALIST_ID)
    assert _first_text(cb.message.edit_text) == messages.clients.empty_active
    cb.answer.assert_awaited_once()
    assert state.state is None


# --- listing & card -----------------------------------------------------------


async def test_show_active_with_clients(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    await _seed_client(session_factory, child_name="Аня")
    await _seed_client(session_factory, child_name="Боря")
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:active:0")
    await h.show_active(cb, _SPECIALIST_ID)
    assert _first_text(cb.message.edit_text) == messages.clients.list_active_title
    assert "Аня" in _button_texts(_markup(cb.message.edit_text))


async def test_show_active_pagination(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
):
    await _seed_specialist(session_factory)
    monkeypatch.setattr("src.bot.handlers.clients._ACTIVE_PAGE_SIZE", 2)
    for i in range(3):
        await _seed_client(session_factory, child_name=f"Z{i}")
    h = _handlers(messages, session_factory)

    cb0 = _fake_callback("clients:active:0")
    await h.show_active(cb0, _SPECIALIST_ID)
    t0 = _button_texts(_markup(cb0.message.edit_text))
    assert "▶" in t0
    assert "◀" not in t0

    cb1 = _fake_callback("clients:active:1")
    await h.show_active(cb1, _SPECIALIST_ID)
    t1 = _button_texts(_markup(cb1.message.edit_text))
    assert "◀" in t1
    assert "▶" not in t1


async def test_show_archive_first_page_shows_date(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_client(
        session_factory, child_name="Архивный", status=ClientStatus.ARCHIVED
    )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:list:archived")
    await h.show_archive(cb, _SPECIALIST_ID)
    assert _first_text(cb.message.edit_text) == messages.clients.archive_title.format(
        page=1
    )
    labels = _button_texts(_markup(cb.message.edit_text))
    assert any(t.startswith("Архивный · ") for t in labels)  # имя + дата


async def test_show_archive_empty(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:list:archived")
    await h.show_archive(cb, _SPECIALIST_ID)
    assert _first_text(cb.message.edit_text) == messages.clients.empty_archived


async def test_show_archive_row_without_date(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        await SqlAlchemyClientsRepo(session).set_status(
            client.id,
            _SPECIALIST_ID,
            ClientStatus.ARCHIVED,
            archived_at=None,
            updated_at=datetime.now(UTC),
        )
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:list:archived")
    await h.show_archive(cb, _SPECIALIST_ID)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert "Петя" in labels  # без даты — только имя, без " · "


async def test_show_archive_pagination(
    messages: BotMessages,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
):
    monkeypatch.setattr("src.bot.handlers.clients._ARCHIVE_PAGE_SIZE", 2)
    for i in range(3):
        await _seed_client(
            session_factory, child_name=f"Z{i}", status=ClientStatus.ARCHIVED
        )
    h = _handlers(messages, session_factory)

    cb0 = _fake_callback("clients:list:archived")
    await h.show_archive(cb0, _SPECIALIST_ID)
    t0 = _button_texts(_markup(cb0.message.edit_text))
    assert "▶" in t0
    assert "◀" not in t0

    cb1 = _fake_callback("clients:arch:1")
    await h.show_archive(cb1, _SPECIALIST_ID)
    t1 = _button_texts(_markup(cb1.message.edit_text))
    assert "◀" in t1
    assert "▶" not in t1


async def test_show_card_renders(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Петя")
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    text = _first_text(cb.message.edit_text)
    assert "Петя" in text
    # No future appointments yet → empty-future line shown inline.
    assert messages.schedule.client_future_empty in text


async def test_show_card_shows_future_appointments(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Лиза")
    assert client.id is not None
    async with session_factory() as session:
        now = datetime.now(UTC)
        await SqlAlchemyAppointmentsRepo(session).add(
            Appointment(
                id=None,
                specialist_id=_SPECIALIST_ID,
                client_id=client.id,
                starts_at=datetime(2030, 1, 15, 9, 0, tzinfo=UTC),  # 14:00 +05
                comment="пробное",
                created_at=now,
                updated_at=now,
            )
        )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    text = _first_text(cb.message.edit_text)
    # No future-header text — the buttons make it clear; no "empty" note either.
    assert messages.schedule.client_future_empty not in text
    markup = _markup(cb.message.edit_text)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    # The future appointment is a button (opens its card to reschedule/cancel).
    assert any("14:00" in label and "пробное" in label for label in labels)
    assert any(c and c.startswith("sched:card:1~") for c in cbs)
    # Action buttons: edit, archive (confirm), history.
    assert f"clients:edit:{client.id}" in cbs
    assert f"clients:archiveask:{client.id}" in cbs
    assert f"sched:chist:{client.id}:0" in cbs


async def test_show_card_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:card:999")
    await h.show_card(cb, _SPECIALIST_ID)
    cb.answer.assert_awaited_once_with(messages.clients.not_found, show_alert=True)
    cb.message.edit_text.assert_not_awaited()


# --- deferred notifications block --------------------------------------------


async def _enqueue_deferred(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    specialist_id: int = _SPECIALIST_ID,
    text: str = "Вы записаны на 16 июня в 14:00.",
) -> int:
    async with session_factory() as session:
        inserted, _ = await SqlAlchemyScheduledMessagesRepo(
            session
        ).enqueue_superseding(
            ScheduledClientMessage(
                id=None,
                specialist_id=specialist_id,
                client_id=client_id,
                chat_id=555,
                text=text,
                target_key=f"appt:{client_id}",
                event=AuditEvent.NOTIFY_CREATED,
                due_at=datetime(2030, 1, 15, 15, 0, tzinfo=UTC),
                status=ScheduledMessageStatus.QUEUED,
                created_at=datetime.now(UTC),
                sent_at=None,
            )
        )
    assert inserted.id is not None
    return inserted.id


async def test_card_shows_deferred_block(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Петя")
    assert client.id is not None
    message_id = await _enqueue_deferred(session_factory, client_id=client.id)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    text = _first_text(cb.message.edit_text)
    assert messages.clients.dnotify_title in text
    cbs = [
        b.callback_data
        for row in _markup(cb.message.edit_text).inline_keyboard
        for b in row
    ]
    assert f"clients:dnotify:cancel:{client.id}:{message_id}" in cbs


async def test_card_hides_deferred_block_when_empty(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Петя")
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    assert messages.clients.dnotify_title not in _first_text(cb.message.edit_text)


async def test_cancel_deferred_notify_removes_row(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, child_name="Петя")
    assert client.id is not None
    message_id = await _enqueue_deferred(session_factory, client_id=client.id)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:dnotify:cancel:{client.id}:{message_id}")
    await h.cancel_deferred_notify(cb, _SPECIALIST_ID)
    # The card re-renders without the block, and the row is gone from the queue.
    assert messages.clients.dnotify_title not in _first_text(cb.message.edit_text)
    async with session_factory() as session:
        remaining = await SqlAlchemyScheduledMessagesRepo(
            session
        ).list_queued_for_client(_SPECIALIST_ID, client.id)
    assert remaining == []


async def test_cancel_deferred_notify_foreign_noop(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    # A second specialist who does not own the row.
    async with session_factory() as session:
        other = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert other.id is not None
    client = await _seed_client(session_factory, child_name="Петя")
    assert client.id is not None
    message_id = await _enqueue_deferred(session_factory, client_id=client.id)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:dnotify:cancel:{client.id}:{message_id}")
    await h.cancel_deferred_notify(cb, other.id)
    async with session_factory() as session:
        remaining = await SqlAlchemyScheduledMessagesRepo(
            session
        ).list_queued_for_client(_SPECIALIST_ID, client.id)
    assert len(remaining) == 1


# --- invite to bot ------------------------------------------------------------


async def test_card_shows_invite_button_when_not_linked(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    markup = _markup(cb.message.edit_text)
    labels = _button_texts(markup)
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert messages.clients.invite_button in labels
    assert f"clients:invite:{client.id}" in cbs


async def test_card_shows_linked_label_after_binding(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    assert client.id is not None
    async with session_factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        await repo.set_invite_token(client.id, _SPECIALIST_ID, "tok", updated_at=now())
        await repo.link_telegram(
            client.id,
            telegram_chat_id=42,
            username=None,
            linked_at=now(),
            updated_at=now(),
        )
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    labels = _button_texts(_markup(cb.message.edit_text))
    assert messages.clients.invite_button_linked in labels


async def test_archived_card_has_no_invite_button(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, status=ClientStatus.ARCHIVED)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:card:{client.id}")
    await h.show_card(cb, _SPECIALIST_ID)
    cbs = [
        b.callback_data
        for row in _markup(cb.message.edit_text).inline_keyboard
        for b in row
    ]
    assert all(not (c and c.startswith("clients:invite:")) for c in cbs)


async def test_send_invite_sends_forwardable_link(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:invite:{client.id}")
    await h.send_invite(cb, _SPECIALIST_ID)
    sent = _first_text(cb.message.answer)
    assert "https://t.me/test_bot?start=cli_" in sent
    cb.answer.assert_awaited_once()

    # The card now carries a persisted token.
    async with session_factory() as session:
        assert client.id is not None
        stored = await SqlAlchemyClientsRepo(session).get_for_specialist(
            client.id, _SPECIALIST_ID
        )
    assert stored is not None
    assert stored.invite_token is not None


async def test_send_invite_reuses_link(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb1 = _fake_callback(f"clients:invite:{client.id}")
    await h.send_invite(cb1, _SPECIALIST_ID)
    cb2 = _fake_callback(f"clients:invite:{client.id}")
    await h.send_invite(cb2, _SPECIALIST_ID)
    assert _first_text(cb1.message.answer) == _first_text(cb2.message.answer)


async def test_send_invite_not_found(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:invite:999")
    await h.send_invite(cb, _SPECIALIST_ID)
    cb.answer.assert_awaited_once_with(messages.clients.not_found, show_alert=True)
    cb.message.answer.assert_not_awaited()


# --- archive / restore --------------------------------------------------------


async def test_ask_archive_shows_confirmation(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:archiveask:{client.id}")
    await h.ask_archive(cb)
    assert _first_text(cb.message.edit_text) == messages.clients.archive_confirm
    cbs = [
        b.callback_data
        for row in _markup(cb.message.edit_text).inline_keyboard
        for b in row
    ]
    assert f"clients:archive:{client.id}" in cbs  # confirm
    assert f"clients:card:{client.id}" in cbs  # cancel → back to card
    # The confirmation step alone must not archive the client.
    async with session_factory() as session:
        assert client.id is not None
        stored = await SqlAlchemyClientsRepo(session).get_for_specialist(
            client.id, _SPECIALIST_ID
        )
    assert stored is not None
    assert stored.status is ClientStatus.ACTIVE


async def test_archive_moves_to_archive(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:archive:{client.id}")
    await h.archive(cb, _SPECIALIST_ID)

    async with session_factory() as session:
        assert client.id is not None
        stored = await SqlAlchemyClientsRepo(session).get_for_specialist(
            client.id, _SPECIALIST_ID
        )
    assert stored is not None
    assert stored.status is ClientStatus.ARCHIVED
    cb.message.edit_text.assert_awaited_once()


async def test_restore_moves_to_active(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory, status=ClientStatus.ARCHIVED)
    h = _handlers(messages, session_factory)
    cb = _fake_callback(f"clients:restore:{client.id}")
    await h.restore(cb, _SPECIALIST_ID)

    async with session_factory() as session:
        assert client.id is not None
        stored = await SqlAlchemyClientsRepo(session).get_for_specialist(
            client.id, _SPECIALIST_ID
        )
    assert stored is not None
    assert stored.status is ClientStatus.ACTIVE


# --- add wizard ---------------------------------------------------------------


async def test_start_add_sets_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state()
    await h.start_add(cb, state)
    assert state.state == AddClient.child_name
    assert _first_text(cb.message.edit_text) == messages.clients.ask_child_name


async def test_add_child_name_empty_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    msg = _fake_message("   ")
    state = _state(state=AddClient.child_name)
    await h.add_child_name(msg, state)
    assert _first_text(msg.answer) == messages.clients.empty_required
    assert state.state == AddClient.child_name


async def test_add_child_name_ok(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    msg = _fake_message("Петя")
    state = _state()
    await h.add_child_name(msg, state)
    assert state.store["child_name"] == "Петя"
    assert state.state == AddClient.contact_name
    assert _first_text(msg.answer) == messages.clients.ask_contact_name


async def test_add_contact_name_empty_reasks(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    msg = _fake_message("")
    state = _state(state=AddClient.contact_name)
    await h.add_contact_name(msg, state)
    assert _first_text(msg.answer) == messages.clients.empty_required


async def test_add_contact_name_ok(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    msg = _fake_message("Мама")
    state = _state()
    await h.add_contact_name(msg, state)
    assert state.store["contact_name"] == "Мама"
    assert state.state == AddClient.contact_phone
    assert _first_text(msg.answer) == messages.clients.ask_phone


async def test_add_phone_stores_and_advances(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    msg = _fake_message("89161234567")
    state = _state()
    await h.add_phone(msg, state)
    assert state.store["contact_phone"] == "89161234567"
    assert state.state == AddClient.contact_telegram
    assert _first_text(msg.answer) == messages.clients.ask_telegram


async def test_skip_phone_advances(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state()
    await h.skip_phone(cb, state)
    assert state.store["contact_phone"] is None
    assert state.state == AddClient.contact_telegram


async def test_add_telegram_creates_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("@masha")
    state = _state(
        data={
            "child_name": "Петя",
            "contact_name": "Мама",
            "contact_phone": "89161234567",
        }
    )
    await h.add_telegram(msg, state, _SPECIALIST_ID)
    assert msg.answer.await_args_list[0].args[0] == messages.clients.added
    assert state.store == {}

    async with session_factory() as session:
        clients = await list_clients(
            SqlAlchemyClientsRepo(session),
            specialist_id=_SPECIALIST_ID,
            status=ClientStatus.ACTIVE,
        )
    assert [c.child_name for c in clients] == ["Петя"]


async def test_skip_telegram_creates_via_callback_message(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(
        data={
            "child_name": "Петя",
            "contact_name": "Мама",
            "contact_phone": "89161234567",
        }
    )
    await h.skip_telegram(cb, state, _SPECIALIST_ID)
    assert cb.message.answer.await_args_list[0].args[0] == messages.clients.added
    cb.answer.assert_awaited_once()


async def test_create_without_contact_channel_reasks_phone(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(
        data={
            "child_name": "Петя",
            "contact_name": "Мама",
            "contact_phone": None,
        }
    )
    await h.skip_telegram(cb, state, _SPECIALIST_ID)
    assert (
        cb.message.answer.await_args_list[0].args[0]
        == messages.clients.need_contact_channel
    )
    assert state.state == AddClient.contact_phone


async def test_cancel_clears_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    h = _handlers(messages, session_factory)
    cb = _fake_callback()
    state = _state(data={"child_name": "Петя"}, state=AddClient.contact_name)
    await h.cancel(cb, state, _SPECIALIST_ID)
    assert state.store == {}
    assert _first_text(cb.message.edit_text) == messages.clients.cancelled


# --- edit a field -------------------------------------------------------------


async def test_start_edit_shows_field_picker(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:edit:5")
    await h.start_edit(cb)
    cb.message.edit_reply_markup.assert_awaited_once()
    cb.answer.assert_awaited_once()


async def test_pick_field_sets_waiting_state(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    h = _handlers(messages, session_factory)
    cb = _fake_callback("clients:setfield:5:note")
    state = _state()
    await h.pick_field(cb, state)
    assert state.state == EditClient.waiting_value
    assert state.store == {"client_id": 5, "field": "note"}


async def test_apply_edit_updates_field(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("важно")
    state = _state(data={"client_id": client.id, "field": "note"})
    await h.apply_edit(msg, state, _SPECIALIST_ID)
    assert _first_text(msg.answer) == messages.clients.updated
    assert state.store == {}

    async with session_factory() as session:
        assert client.id is not None
        stored = await SqlAlchemyClientsRepo(session).get_for_specialist(
            client.id, _SPECIALIST_ID
        )
    assert stored is not None
    assert stored.note == "важно"


async def test_apply_edit_rejects_empty_required(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    msg = _fake_message("   ")
    state = _state(data={"client_id": client.id, "field": "child_name"})
    await h.apply_edit(msg, state, _SPECIALIST_ID)
    assert _first_text(msg.answer) == messages.clients.empty_required
    assert state.store != {}


# --- middleware ---------------------------------------------------------------


async def test_middleware_injects_specialist_id(
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        repo = SqlAlchemySpecialistsRepo(session)
        specialist = await create_invite(repo)
        await consume_invite(repo, specialist.invite_token, chat_id=555, username=None)

    middleware = SpecialistMiddleware(session_factory)
    captured: dict[str, Any] = {}

    async def handler(_event: object, data: dict[str, Any]) -> str:  # noqa: RUF029
        captured["specialist_id"] = data["specialist_id"]
        return "ok"

    data = {"event_from_user": _user(555)}
    result = await middleware(handler, cast("TelegramObject", object()), data)
    assert result == "ok"
    assert captured["specialist_id"] == specialist.id


async def test_middleware_drops_unknown_user(
    session_factory: async_sessionmaker[AsyncSession],
):
    middleware = SpecialistMiddleware(session_factory)
    called = False

    async def handler(_event: object, _data: dict[str, Any]) -> str:  # noqa: RUF029
        nonlocal called
        called = True
        return "ok"

    data = {"event_from_user": _user(404)}
    result = await middleware(handler, cast("TelegramObject", object()), data)
    assert result is None
    assert called is False


def _user(user_id: int) -> AsyncMock:
    user = AsyncMock()
    user.id = user_id
    return user


# --- navigation builders (re-open a target from another router) ---------------


async def test_nav_active_renders_active_list(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    text, keyboard = await h.nav_active(_SPECIALIST_ID, "clients:active:0")
    assert text == messages.clients.list_active_title
    assert client.child_name in _button_texts(keyboard)


async def test_nav_archive_renders_archive_list(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    await _seed_client(session_factory, status=ClientStatus.ARCHIVED)
    h = _handlers(messages, session_factory)
    text, _ = await h.nav_archive(_SPECIALIST_ID, "clients:arch:0")
    assert text == messages.clients.archive_title.format(page=1)


async def test_nav_card_parses_id_and_renders_card(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client = await _seed_client(session_factory)
    h = _handlers(messages, session_factory)
    text, keyboard = await h.nav_card(
        _SPECIALIST_ID, f"clients:card:{client.id}~clients:active:0"
    )
    assert client.child_name in text
    # The card's own Back honours the inner target threaded through the prefix.
    assert "clients:active:0" in [
        b.callback_data for row in keyboard.inline_keyboard for b in row
    ]
