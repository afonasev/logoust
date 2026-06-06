from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.schedule import utc_to_wall
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import SqlAlchemySubscriptionsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite
from src.services.payment_reminder import (
    PaymentReminderAlert,
    run_payment_reminders_if_due,
)
from src.services.subscriptions import create_subscription, decrement_meeting

_TZ = "Asia/Yekaterinburg"  # UTC+5
_SP = 1
_CHAT = 555
# 08:00 UTC → 13:00 wall on 2026-06-15 (>= 12:00); tomorrow is 2026-06-16.
_NOW = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
_TOMORROW = date(2026, 6, 16)


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]):
    async with factory() as session:
        return await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_client(
    factory: async_sessionmaker[AsyncSession],
    *,
    chat_id: int | None = _CHAT,
    child: str = "Петя",
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
        if chat_id is not None:
            await repo.link_telegram(
                client.id,
                telegram_chat_id=chat_id,
                username=None,
                linked_at=_NOW,
                updated_at=_NOW,
            )
    return client.id


async def _seed_appointment(
    factory: async_sessionmaker[AsyncSession], client_id: int, hhmm: str = "10:00"
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


async def _seed_subscription(
    factory: async_sessionmaker[AsyncSession],
    client_id: int,
    *,
    meetings: int,
    spend: int = 0,
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
        for _ in range(spend):
            await decrement_meeting(
                SqlAlchemySubscriptionsRepo(session),
                subscription_id=sub.id,
                specialist_id=_SP,
            )
    return sub.id


async def _run(factory: async_sessionmaker[AsyncSession], specialist, now=_NOW):
    collected: list[PaymentReminderAlert] = []

    async def alert(payload: PaymentReminderAlert) -> None:  # noqa: RUF029
        collected.append(payload)

    async with factory() as session:
        await run_payment_reminders_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            subscriptions_repo=SqlAlchemySubscriptionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            alert=alert,
        )
    return collected


async def _last_run_on(factory: async_sessionmaker[AsyncSession]) -> date | None:
    async with factory() as session:
        reloaded = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert reloaded is not None
    return reloaded.payment_reminder_last_run_on


async def test_linked_client_empty_sub_alerts_and_marks(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    sub_id = await _seed_subscription(session_factory, client_id, meetings=1, spend=1)

    alerts = await _run(session_factory, specialist)
    assert len(alerts) == 1
    assert alerts[0].child_name == "Петя"
    assert alerts[0].chat_id == _CHAT  # linked
    assert alerts[0].client_id == client_id
    # Subscription marked reminded; day stamped.
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
    assert sub is not None
    assert sub.payment_reminded_at is not None
    assert await _last_run_on(session_factory) == date(2026, 6, 15)


async def test_unlinked_client_alerts_without_chat_and_marks(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=None)
    await _seed_appointment(session_factory, client_id)
    sub_id = await _seed_subscription(session_factory, client_id, meetings=1, spend=1)

    alerts = await _run(session_factory, specialist)
    assert len(alerts) == 1
    assert alerts[0].chat_id is None  # not linked → no send button
    async with session_factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
    assert sub is not None
    assert sub.payment_reminded_at is not None


async def test_remaining_positive_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    await _seed_subscription(session_factory, client_id, meetings=4)  # remaining 4

    alerts = await _run(session_factory, specialist)
    assert alerts == []
    # Day is still stamped even when nothing is alerted.
    assert await _last_run_on(session_factory) == date(2026, 6, 15)


async def test_no_appointment_tomorrow_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_subscription(session_factory, client_id, meetings=1, spend=1)

    alerts = await _run(session_factory, specialist)
    assert alerts == []
    assert await _last_run_on(session_factory) == date(2026, 6, 15)


async def test_no_active_subscription_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)

    alerts = await _run(session_factory, specialist)
    assert alerts == []


async def test_already_reminded_no_alert(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    sub_id = await _seed_subscription(session_factory, client_id, meetings=1, spend=1)
    async with session_factory() as session:
        await SqlAlchemySubscriptionsRepo(session).mark_payment_reminded(
            sub_id, datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
        )

    alerts = await _run(session_factory, specialist)
    assert alerts == []


async def test_multiple_appointments_one_alert(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id, hhmm="14:00")
    await _seed_appointment(session_factory, client_id, hhmm="10:00")
    await _seed_subscription(session_factory, client_id, meetings=1, spend=1)

    alerts = await _run(session_factory, specialist)
    assert len(alerts) == 1
    # The earliest appointment instant is carried (10:00 wall).
    assert f"{utc_to_wall(alerts[0].starts_at, _TZ):%H:%M}" == "10:00"


async def test_not_due_does_nothing_and_does_not_stamp(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    await _seed_subscription(session_factory, client_id, meetings=1, spend=1)

    # 04:00 UTC → 09:00 wall, before 12:00 → not due.
    early = datetime(2026, 6, 15, 4, 0, tzinfo=UTC)
    alerts = await _run(session_factory, specialist, now=early)
    assert alerts == []
    assert await _last_run_on(session_factory) is None
