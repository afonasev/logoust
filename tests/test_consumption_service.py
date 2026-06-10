from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.schedule import today_in_tz
from src.domain.specialist import Specialist
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.infrastructure.subscriptions_repo import (
    SqlAlchemySubscriptionDeductionsRepo,
    SqlAlchemySubscriptionsRepo,
)
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.consumption import (
    ConsumptionReport,
    MissReason,
    run_consumption,
    run_consumption_if_due,
)
from src.services.invites import create_invite
from src.services.recurring import add_slot, create_schedule
from src.services.subscriptions import create_subscription, list_deductions

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST
_SP = 1
# 15:00 UTC → 20:00 wall on 2026-06-15, exactly consumption_time → due.
_NOW = datetime(2026, 6, 15, 15, 0, tzinfo=UTC)


async def _seed_specialist(factory: async_sessionmaker[AsyncSession]) -> Specialist:
    async with factory() as session:
        return await create_invite(SqlAlchemySpecialistsRepo(session))


async def _seed_client(
    factory: async_sessionmaker[AsyncSession], *, child: str = "Петя"
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


async def _seed_appointment(
    factory: async_sessionmaker[AsyncSession],
    client_id: int,
    *,
    hhmm: str = "10:00",
) -> None:
    async with factory() as session:
        await create_appointment(
            SqlAlchemyAppointmentsRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            day=today_in_tz(_NOW, _TZ),
            hhmm=hhmm,
            comment="разовая",
            tz=_TZ,
            now=_NOW,
        )


async def _seed_today_series(
    factory: async_sessionmaker[AsyncSession], client_id: int, *, hhmm: str = "11:00"
) -> None:
    today = today_in_tz(_NOW, _TZ)
    async with factory() as session:
        schedule = await create_schedule(
            SqlAlchemyRecurringScheduleRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            comment="регулярная",
            now=_NOW,
        )
        assert schedule.id is not None
        await add_slot(
            SqlAlchemyRecurringSlotRepo(session),
            schedule_id=schedule.id,
            weekday=today.weekday(),
            time_hhmm=hhmm,
            tz=_TZ,
            now=_NOW,
            start_date=today,
        )


async def _run(
    factory: async_sessionmaker[AsyncSession], specialist: Specialist
) -> ConsumptionReport:
    async with factory() as session:
        return await run_consumption(
            specialist,
            _NOW,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            subscriptions_repo=SqlAlchemySubscriptionsRepo(session),
            deductions_repo=SqlAlchemySubscriptionDeductionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
        )


async def _run_if_due(
    factory: async_sessionmaker[AsyncSession],
    specialist: Specialist,
    now: datetime = _NOW,
) -> list[ConsumptionReport]:
    collected: list[ConsumptionReport] = []

    async def report(payload: ConsumptionReport) -> None:  # noqa: RUF029
        collected.append(payload)

    async with factory() as session:
        await run_consumption_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            subscriptions_repo=SqlAlchemySubscriptionsRepo(session),
            deductions_repo=SqlAlchemySubscriptionDeductionsRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            report=report,
        )
    return collected


async def _remaining(factory, sub_id: int) -> int:
    async with factory() as session:
        sub = await SqlAlchemySubscriptionsRepo(session).get_for_specialist(sub_id, _SP)
    assert sub is not None
    return sub.remaining


async def test_deducts_passed_meeting(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    sub_id = await _seed_subscription(factory, client_id, meetings=5)
    await _seed_appointment(factory, client_id)

    report = await _run(factory, specialist)

    assert len(report.deducted) == 1
    assert report.deducted[0].remaining == 4
    assert not report.missed
    assert await _remaining(factory, sub_id) == 4
    async with factory() as s:
        journal = await list_deductions(
            SqlAlchemySubscriptionDeductionsRepo(s), subscription_id=sub_id
        )
    assert len(journal) == 1
    assert journal[0].appointment_id is not None
    assert journal[0].appointment_comment == "разовая"


async def test_missed_no_subscription(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    await _seed_appointment(factory, client_id)

    report = await _run(factory, specialist)

    assert not report.deducted
    assert len(report.missed) == 1
    assert report.missed[0].reason is MissReason.NO_SUBSCRIPTION
    assert report.missed[0].client_id == client_id
    assert report.missed[0].subscription_id is None


async def test_missed_exhausted(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    sub_id = await _seed_subscription(factory, client_id, meetings=1)
    # Drain the only meeting via a manual deduction so the active one is exhausted.
    async with factory() as s:
        await SqlAlchemySubscriptionDeductionsRepo(s).add_manual(
            subscription_id=sub_id, specialist_id=_SP, created_at=_NOW
        )
    await _seed_appointment(factory, client_id)

    report = await _run(factory, specialist)

    assert not report.deducted
    assert len(report.missed) == 1
    assert report.missed[0].reason is MissReason.EXHAUSTED
    assert report.missed[0].client_id == client_id
    assert report.missed[0].subscription_id == sub_id
    assert await _remaining(factory, sub_id) == 0


async def test_future_meeting_not_deducted(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    await _seed_subscription(factory, client_id, meetings=5)
    # 23:00 wall today is after now (20:00) → not yet passed.
    await _seed_appointment(factory, client_id, hhmm="23:00")

    report = await _run(factory, specialist)
    assert report.is_empty


async def test_two_meetings_same_day_one_subscription(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    sub_id = await _seed_subscription(factory, client_id, meetings=2)
    await _seed_appointment(factory, client_id, hhmm="09:00")
    await _seed_appointment(factory, client_id, hhmm="10:00")

    report = await _run(factory, specialist)

    assert len(report.deducted) == 2
    assert await _remaining(factory, sub_id) == 0
    async with factory() as s:
        journal = await list_deductions(
            SqlAlchemySubscriptionDeductionsRepo(s), subscription_id=sub_id
        )
    assert len(journal) == 2


async def test_virtual_repeat_materialized_then_deducted(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    sub_id = await _seed_subscription(factory, client_id, meetings=5)
    await _seed_today_series(factory, client_id, hhmm="11:00")

    report = await _run(factory, specialist)

    assert len(report.deducted) == 1
    assert await _remaining(factory, sub_id) == 4
    # The virtual repeat is now a real row carrying the appointment link.
    async with factory() as s:
        journal = await list_deductions(
            SqlAlchemySubscriptionDeductionsRepo(s), subscription_id=sub_id
        )
    assert journal[0].appointment_id is not None


async def test_two_passes_one_deduction(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    sub_id = await _seed_subscription(factory, client_id, meetings=5)
    await _seed_appointment(factory, client_id)

    await _run(factory, specialist)
    await _run(factory, specialist)  # second pass over the same meeting

    assert await _remaining(factory, sub_id) == 4
    async with factory() as s:
        journal = await list_deductions(
            SqlAlchemySubscriptionDeductionsRepo(s), subscription_id=sub_id
        )
    assert len(journal) == 1


async def test_if_due_reports_and_stamps(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    client_id = await _seed_client(factory)
    await _seed_subscription(factory, client_id, meetings=5)
    await _seed_appointment(factory, client_id)

    reports = await _run_if_due(factory, specialist)

    assert len(reports) == 1
    assert len(reports[0].deducted) == 1
    async with factory() as s:
        stamped = await SqlAlchemySpecialistsRepo(s).get(_SP)
    assert stamped is not None
    assert stamped.consumption_last_run_on == today_in_tz(_NOW, _TZ)


async def test_if_due_empty_evening_silent_but_stamped(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)

    reports = await _run_if_due(factory, specialist)

    # Nothing to show → no report sent, but the day is stamped done.
    assert reports == []
    async with factory() as s:
        stamped = await SqlAlchemySpecialistsRepo(s).get(_SP)
    assert stamped is not None
    assert stamped.consumption_last_run_on == today_in_tz(_NOW, _TZ)


async def test_if_due_not_due_does_not_run(session_factory):
    factory = session_factory
    specialist = await _seed_specialist(factory)
    # 14:00 UTC → 19:00 wall, before 20:00 consumption_time.
    not_yet = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)

    reports = await _run_if_due(factory, specialist, now=not_yet)

    assert reports == []
    async with factory() as s:
        unstamped = await SqlAlchemySpecialistsRepo(s).get(_SP)
    assert unstamped is not None
    assert unstamped.consumption_last_run_on is None
