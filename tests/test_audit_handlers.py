from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.audit import AuditHandlers
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite


def _fake_message() -> AsyncMock:
    msg = AsyncMock()
    msg.answer = AsyncMock()
    return msg


def _fake_callback(data: str) -> AsyncMock:
    cb = AsyncMock()
    cb.data = data
    cb.message = AsyncMock()
    return cb


def _answer_text(msg: AsyncMock) -> str:
    return msg.answer.await_args.args[0]


def _answer_keyboard(msg: AsyncMock):
    return msg.answer.await_args.kwargs["reply_markup"]


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        specialist = await create_invite(SqlAlchemySpecialistsRepo(session))
    assert specialist.id is not None
    return specialist.id


async def _seed_client(factory: async_sessionmaker[AsyncSession], sp_id: int) -> int:
    async with factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=sp_id,
                child_name="Маша",
                contact_name="Мама",
                contact_phone="+70000000000",
            ),
        )
    assert client.id is not None
    return client.id


async def test_show_empty_journal(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    h = AuditHandlers(messages, session_factory)
    msg = _fake_message()
    await h.show(msg, sp_id)
    assert _answer_text(msg) == messages.audit.empty
    assert _answer_keyboard(msg) is None


async def test_show_renders_message_and_action_distinctly(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, sp_id)
    async with session_factory() as session:
        repo = SqlAlchemyAuditRepo(session)
        await repo.record(
            specialist_id=sp_id,
            kind=AuditKind.ACTION,
            event=AuditEvent.CLIENT_CREATED,
            client_id=client_id,
        )
        await repo.record(
            specialist_id=sp_id,
            kind=AuditKind.MESSAGE,
            event=AuditEvent.NOTIFY_CREATED,
            client_id=client_id,
            text="Запись создана на 10:00",
            status=DeliveryStatus.SENT,
        )
    h = AuditHandlers(messages, session_factory)
    msg = _fake_message()
    await h.show(msg, sp_id)
    text = _answer_text(msg)
    assert messages.audit.title in text
    assert messages.audit.events["notify_created"] in text
    assert messages.audit.events["client_created"] in text
    assert "Маша" in text  # client name resolved
    assert "Запись создана на 10:00" in text  # message text rendered
    assert messages.audit.status_sent in text  # delivered icon
    assert messages.audit.action_icon in text  # action icon


async def test_failed_message_shows_failure_icon(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    async with session_factory() as session:
        await SqlAlchemyAuditRepo(session).record(
            specialist_id=sp_id,
            kind=AuditKind.MESSAGE,
            event=AuditEvent.WELCOME,
            text="Здравствуйте!",
            status=DeliveryStatus.FAILED,
            error="bot blocked",
        )
    h = AuditHandlers(messages, session_factory)
    msg = _fake_message()
    await h.show(msg, sp_id)
    text = _answer_text(msg)
    assert messages.audit.status_failed in text
    assert messages.audit.events["welcome"] in text
    assert "Здравствуйте!" in text


async def test_pagination_buttons_and_paging(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    sp_id = await _seed_specialist(session_factory)
    async with session_factory() as session:
        repo = SqlAlchemyAuditRepo(session)
        for _ in range(11):  # one more than the page size of 10
            await repo.record(
                specialist_id=sp_id,
                kind=AuditKind.ACTION,
                event=AuditEvent.APPT_CREATED,
            )
    h = AuditHandlers(messages, session_factory)

    # Page 0: newest 10, only the "older" button.
    msg = _fake_message()
    await h.show(msg, sp_id)
    keyboard = _answer_keyboard(msg)
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == [messages.audit.btn_next]

    # Page 1: the remaining row, only the "newer" button.
    cb = _fake_callback("audit:page:1")
    await h.page(cb, sp_id)
    cb.message.edit_text.assert_awaited_once()
    keyboard = cb.message.edit_text.await_args.kwargs["reply_markup"]
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == [messages.audit.btn_prev]
    cb.answer.assert_awaited_once()
