from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.handlers.payment import PaymentHandlers
from src.bot.messages import BotMessages
from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite

_SP = 1


def _fake_callback(data: str) -> AsyncMock:
    cb = AsyncMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.bot = AsyncMock()
    cb.bot.send_message = AsyncMock()
    return cb


def _texts(mock: AsyncMock) -> list[str]:
    return [c.args[0] for c in mock.await_args_list]


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int | None
) -> int:
    now = datetime.now(UTC)
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
        if chat_id is not None:
            await repo.link_telegram(
                client.id,
                telegram_chat_id=chat_id,
                username=None,
                linked_at=now,
                updated_at=now,
            )
    return client.id


async def _audit_messages(
    factory: async_sessionmaker[AsyncSession],
) -> list[tuple[AuditEvent, DeliveryStatus | None]]:
    async with factory() as session:
        rows = await SqlAlchemyAuditRepo(session).list_for_specialist(
            _SP, limit=50, offset=0
        )
    return [(r.event, r.status) for r in rows if r.kind is AuditKind.MESSAGE]


async def test_send_payment_reminder_delivers_and_audits(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    h = PaymentHandlers(messages, session_factory)
    cb = _fake_callback(f"pay:send:{client_id}")
    await h.send_payment_reminder(cb, _SP)
    cb.bot.send_message.assert_awaited_once()
    assert cb.bot.send_message.await_args.args[0] == 555
    assert _texts(cb.answer)[0] == messages.payment.sent
    assert await _audit_messages(session_factory) == [
        (AuditEvent.PAYMENT_REMINDER, DeliveryStatus.SENT)
    ]


async def test_send_payment_reminder_failure_records_failed(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=555)
    h = PaymentHandlers(messages, session_factory)
    cb = _fake_callback(f"pay:send:{client_id}")
    cb.bot.send_message.side_effect = TelegramForbiddenError(
        method=None,  # type: ignore[arg-type]
        message="blocked",
    )
    await h.send_payment_reminder(cb, _SP)
    assert _texts(cb.answer)[0] == messages.payment.not_delivered
    assert await _audit_messages(session_factory) == [
        (AuditEvent.PAYMENT_REMINDER, DeliveryStatus.FAILED)
    ]


async def test_send_payment_reminder_unlinked_client(
    messages: BotMessages, session_factory: async_sessionmaker[AsyncSession]
):
    # Client unlinked since the alert was posted: nothing to send, no audit row.
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=None)
    h = PaymentHandlers(messages, session_factory)
    cb = _fake_callback(f"pay:send:{client_id}")
    await h.send_payment_reminder(cb, _SP)
    cb.bot.send_message.assert_not_awaited()
    assert _texts(cb.answer)[0] == messages.payment.not_linked
    assert await _audit_messages(session_factory) == []
