from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.bot.messages import DEFAULT_MESSAGES_PATH, BotMessages, load_messages
from src.bot.scheduler import (
    run_consumption_pass,
    run_digest_pass,
    run_outbox_pass,
    run_payment_reminder_pass,
    run_reminder_pass,
)
from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.domain.schedule import today_in_tz
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessageStatus,
)
from src.domain.subscription import Subscription, SubscriptionStatus
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite
from src.services.subscriptions import create_subscription

_TZ = "Asia/Yekaterinburg"
_SP = 1
_NOW = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)  # 13:00 wall — past the 12:00 default
_TOMORROW = date(2026, 6, 16)


def _messages() -> BotMessages:
    return load_messages(DEFAULT_MESSAGES_PATH)


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_linked_client(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int, child: str = "Петя"
) -> int:
    async with factory() as session:
        repo = SqlAlchemyClientsRepo(session)
        client = await add_client(
            repo,
            NewClient(
                specialist_id=_SP,
                child_name=child,
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
        assert client.id is not None
        await repo.link_telegram(
            client.id,
            telegram_chat_id=chat_id,
            username=None,
            linked_at=_NOW,
            updated_at=_NOW,
        )
    return client.id


async def _seed_appointment(
    factory: async_sessionmaker[AsyncSession], client_id: int, hhmm: str
) -> None:
    async with factory() as session:
        await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=_TOMORROW,
            hhmm=hhmm,
            comment=None,
            tz=_TZ,
            now=_NOW,
        )


async def test_pass_sends_to_due_client(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 555
    # Journal row created so a second pass would not re-send (10:00 wall = 05:00 UTC).
    starts_at = datetime(2026, 6, 16, 5, 0, tzinfo=UTC)
    async with session_factory() as session:
        statuses = await SqlAlchemyRemindersRepo(session).statuses_for_day(
            _SP, [(client_id, starts_at)]
        )
    assert statuses


async def _audit_messages(
    factory: async_sessionmaker[AsyncSession],
) -> list[tuple[AuditEvent, DeliveryStatus | None]]:
    async with factory() as session:
        rows = await SqlAlchemyAuditRepo(session).list_for_specialist(
            _SP, limit=50, offset=0
        )
    return [(r.event, r.status) for r in rows if r.kind is AuditKind.MESSAGE]


async def test_reminder_delivery_records_audit_message(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    assert await _audit_messages(session_factory) == [
        (AuditEvent.REMINDER, DeliveryStatus.SENT)
    ]


async def test_reminder_failure_records_failed_audit(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=None,  # type: ignore[arg-type]
        message="blocked",
    )
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    assert await _audit_messages(session_factory) == [
        (AuditEvent.REMINDER, DeliveryStatus.FAILED)
    ]


async def test_pass_skips_non_due_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_appointment(session_factory, client_id, "10:00")
    # Before noon wall (05:00 UTC → 10:00 wall): not due.
    bot = AsyncMock()
    await run_reminder_pass(
        bot, session_factory, _messages(), datetime(2026, 6, 15, 5, 0, tzinfo=UTC)
    )
    bot.send_message.assert_not_awaited()


async def test_delivery_failure_does_not_stop_pass(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    a = await _seed_linked_client(session_factory, chat_id=111, child="Аня")
    b = await _seed_linked_client(session_factory, chat_id=222, child="Боря")
    await _seed_appointment(session_factory, a, "10:00")
    await _seed_appointment(session_factory, b, "11:00")
    bot = AsyncMock()
    bot.send_message.side_effect = [
        TelegramForbiddenError(method=None, message="blocked"),  # type: ignore[arg-type]
        None,
    ]
    await run_reminder_pass(bot, session_factory, _messages(), _NOW)
    # Both clients were attempted despite the first one failing.
    assert bot.send_message.await_count == 2


_TODAY = date(2026, 6, 15)


async def _welcome_specialist(
    factory: async_sessionmaker[AsyncSession], *, chat_id: int
) -> None:
    async with factory() as session:
        await SqlAlchemySpecialistsRepo(session).mark_welcomed(
            _SP, telegram_chat_id=chat_id, telegram_username=None, welcomed_at=_NOW
        )


async def _seed_today_appointment(
    factory: async_sessionmaker[AsyncSession], client_id: int, hhmm: str
) -> None:
    async with factory() as session:
        await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=_TODAY,
            hhmm=hhmm,
            comment=None,
            tz=_TZ,
            now=_NOW,
        )


async def test_digest_pass_sends_to_due_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=777)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_today_appointment(session_factory, client_id, "14:00")
    bot = AsyncMock()
    await run_digest_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 777


async def test_digest_pass_skips_non_due_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=777)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_today_appointment(session_factory, client_id, "14:00")
    bot = AsyncMock()
    # 04:00 UTC → 09:00 wall, before the 10:00 digest trigger.
    await run_digest_pass(
        bot, session_factory, _messages(), datetime(2026, 6, 15, 4, 0, tzinfo=UTC)
    )
    bot.send_message.assert_not_awaited()


async def _enqueue_due(  # noqa: PLR0913
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: int,
    chat_id: int,
    due_at: datetime,
    event: AuditEvent = AuditEvent.NOTIFY_CREATED,
    text: str = "Вы записаны.",
) -> int:
    async with factory() as session:
        inserted, _ = await SqlAlchemyScheduledMessagesRepo(
            session
        ).enqueue_superseding(
            ScheduledClientMessage(
                id=None,
                specialist_id=_SP,
                client_id=client_id,
                chat_id=chat_id,
                text=text,
                target_key=f"appt:{client_id}",
                event=event,
                due_at=due_at,
                status=ScheduledMessageStatus.QUEUED,
                created_at=_NOW,
                sent_at=None,
            )
        )
    assert inserted.id is not None
    return inserted.id


async def _queued(factory: async_sessionmaker[AsyncSession], client_id: int) -> list:
    async with factory() as session:
        return await SqlAlchemyScheduledMessagesRepo(session).list_queued_for_client(
            _SP, client_id
        )


_OUTBOX_NOW = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)


async def test_outbox_pass_delivers_due_message(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _enqueue_due(
        session_factory,
        client_id=client_id,
        chat_id=555,
        due_at=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),  # past → due
    )
    bot = AsyncMock()
    await run_outbox_pass(bot, session_factory, _messages(), _OUTBOX_NOW)
    bot.send_message.assert_awaited_once_with(555, "Вы записаны.")
    # The row leaves the queue and the delivery is journalled.
    assert await _queued(session_factory, client_id) == []
    assert await _audit_messages(session_factory) == [
        (AuditEvent.NOTIFY_CREATED, DeliveryStatus.SENT)
    ]


async def test_outbox_pass_skips_future_message(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _enqueue_due(
        session_factory,
        client_id=client_id,
        chat_id=555,
        due_at=datetime(2030, 1, 1, tzinfo=UTC),  # far future → not due
    )
    bot = AsyncMock()
    await run_outbox_pass(bot, session_factory, _messages(), _OUTBOX_NOW)
    bot.send_message.assert_not_awaited()
    assert len(await _queued(session_factory, client_id)) == 1


async def test_outbox_pass_failure_notifies_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=777)
    client_id = await _seed_linked_client(session_factory, chat_id=555, child="Петя")
    await _enqueue_due(
        session_factory,
        client_id=client_id,
        chat_id=555,
        due_at=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
    )
    bot = AsyncMock()
    # The client send fails; the follow-up message to the specialist succeeds.
    bot.send_message.side_effect = [
        TelegramForbiddenError(method=None, message="blocked"),  # type: ignore[arg-type]
        None,
    ]
    await run_outbox_pass(bot, session_factory, _messages(), _OUTBOX_NOW)
    assert bot.send_message.await_count == 2
    specialist_call = bot.send_message.await_args_list[1]
    assert specialist_call.args[0] == 777
    assert "Петя" in specialist_call.args[1]
    # The row is marked failed (out of the queue) and the failure is journalled.
    assert await _queued(session_factory, client_id) == []
    assert await _audit_messages(session_factory) == [
        (AuditEvent.NOTIFY_CREATED, DeliveryStatus.FAILED)
    ]


async def test_outbox_pass_skips_unlinked_client(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    # A client that exists but never linked the bot (no telegram_chat_id).
    async with session_factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=_SP,
                child_name="Аня",
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
    assert client.id is not None
    await _enqueue_due(
        session_factory,
        client_id=client.id,
        chat_id=555,
        due_at=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
    )
    bot = AsyncMock()
    await run_outbox_pass(bot, session_factory, _messages(), _OUTBOX_NOW)
    bot.send_message.assert_not_awaited()
    # The row stays queued (visible/cancellable on the card), not lost.
    assert len(await _queued(session_factory, client.id)) == 1


async def test_digest_pass_logs_failure(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=777)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_today_appointment(session_factory, client_id, "14:00")
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=None,  # type: ignore[arg-type]
        message="blocked",
    )
    # The pass swallows the delivery error; the day is still marked done.
    await run_digest_pass(bot, session_factory, _messages(), _NOW)
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert specialist is not None
    assert specialist.morning_notify_last_run_on == _TODAY


# --- subscription payment reminder pass ---------------------------------------


async def _seed_unlinked_client(
    factory: async_sessionmaker[AsyncSession], child: str = "Петя"
) -> int:
    async with factory() as session:
        client = await add_client(
            SqlAlchemyClientsRepo(session),
            NewClient(
                specialist_id=_SP,
                child_name=child,
                contact_name="Мама",
                contact_phone="89161234567",
            ),
        )
    assert client.id is not None
    return client.id


async def _seed_empty_subscription(
    factory: async_sessionmaker[AsyncSession], client_id: int
) -> None:
    async with factory() as session:
        await SqlAlchemySubscriptionsRepo(session).add(
            Subscription(
                id=None,
                client_id=client_id,
                specialist_id=_SP,
                purchased=4,
                remaining=0,
                status=SubscriptionStatus.ACTIVE,
                created_at=_NOW,
            )
        )


def _callbacks(markup: object) -> list[str | None]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]  # type: ignore[attr-defined]


async def test_payment_pass_alerts_specialist_with_send_button(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_empty_subscription(session_factory, client_id)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_payment_reminder_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 900  # the specialist's chat
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert _callbacks(markup) == [f"pay:send:{client_id}"]
    # Marked reminded + day stamped so a repeat tick / next day won't re-alert.
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_active(client_id, _SP)
        specialist = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert sub is not None
    assert sub.payment_reminded_at is not None
    assert specialist is not None
    assert specialist.payment_reminder_last_run_on == _TODAY


async def test_payment_pass_unlinked_client_alert_without_button(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_unlinked_client(session_factory)
    await _seed_empty_subscription(session_factory, client_id)
    await _seed_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_payment_reminder_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_awaited_once()
    # No telegram link → no "send" button (nowhere to send).
    assert bot.send_message.await_args.kwargs["reply_markup"] is None


async def test_payment_pass_skips_disabled_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    await _seed_empty_subscription(session_factory, client_id)
    await _seed_appointment(session_factory, client_id, "10:00")
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            _SP, {"payment_reminder_enabled": False}
        )
    bot = AsyncMock()
    await run_payment_reminder_pass(bot, session_factory, _messages(), _NOW)
    bot.send_message.assert_not_awaited()


# --- consumption pass ---------------------------------------------------------

# 15:00 UTC → 20:00 wall on 2026-06-15, exactly the default consumption_time.
_CONS_NOW = datetime(2026, 6, 15, 15, 0, tzinfo=UTC)


async def _seed_subscription(
    factory: async_sessionmaker[AsyncSession], client_id: int, *, meetings: int
) -> int:
    async with factory() as session:
        sub = await create_subscription(
            SqlAlchemySubscriptionsRepo(session),
            client_id=client_id,
            specialist_id=_SP,
            meetings=meetings,
        )
    assert sub is not None
    assert sub.id is not None
    return sub.id


async def test_consumption_pass_reports_deductions(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    sub_id = await _seed_subscription(session_factory, client_id, meetings=5)
    await _seed_today_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_consumption_pass(bot, session_factory, _messages(), _CONS_NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 900  # the specialist's chat
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert _callbacks(markup) == [f"subs:card:{sub_id}"]
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
        specialist = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert sub is not None
    assert sub.remaining == 4
    assert specialist is not None
    assert specialist.consumption_last_run_on == today_in_tz(_CONS_NOW, _TZ)


async def test_consumption_pass_missed_has_no_buttons(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    # No subscription → the meeting is reported as a ❗ line, with no buttons.
    await _seed_today_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    await run_consumption_pass(bot, session_factory, _messages(), _CONS_NOW)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["reply_markup"] is None
    text = bot.send_message.await_args.args[1]
    assert _messages().consumption.missed_header in text


async def test_consumption_pass_empty_is_silent_but_stamps(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    bot = AsyncMock()
    await run_consumption_pass(bot, session_factory, _messages(), _CONS_NOW)
    bot.send_message.assert_not_awaited()
    async with session_factory() as session:
        specialist = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert specialist is not None
    assert specialist.consumption_last_run_on == today_in_tz(_CONS_NOW, _TZ)


async def test_consumption_pass_delivery_failure_keeps_deduction(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    client_id = await _seed_linked_client(session_factory, chat_id=555)
    sub_id = await _seed_subscription(session_factory, client_id, meetings=5)
    await _seed_today_appointment(session_factory, client_id, "10:00")
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=None,  # type: ignore[arg-type]
        message="blocked",
    )
    await run_consumption_pass(bot, session_factory, _messages(), _CONS_NOW)
    # Delivery failed, but the deduction is committed and not rolled back.
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
    assert sub is not None
    assert sub.remaining == 4


async def test_consumption_pass_skips_disabled_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    await _welcome_specialist(session_factory, chat_id=900)
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            _SP, {"consumption_enabled": False}
        )
    bot = AsyncMock()
    await run_consumption_pass(bot, session_factory, _messages(), _CONS_NOW)
    bot.send_message.assert_not_awaited()
