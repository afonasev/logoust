from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.domain.schedule import wall_to_utc
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.infrastructure.recurring_repo import (
    SqlAlchemyRecurringScheduleRepo,
    SqlAlchemyRecurringSlotOverrideRepo,
    SqlAlchemyRecurringSlotRepo,
)
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.appointments import create_appointment
from src.services.clients import NewClient, add_client
from src.services.invites import create_invite
from src.services.recurring import add_slot, create_schedule
from src.services.reminder import (
    ReminderMessages,
    apply_reminder_response,
    run_reminders_if_due,
    run_reminders_now,
    status_for_occurrence,
    statuses_for_appointments,
)

_TZ = "Asia/Yekaterinburg"  # UTC+5
_SP = 1
_CHAT = 555
# 08:00 UTC → 13:00 wall on 2026-06-15 (a Monday); tomorrow is 2026-06-16.
_NOW = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
_TOMORROW = date(2026, 6, 16)
_MESSAGES = ReminderMessages(client_text="{child}|{date}|{time}")


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


async def _run(factory: async_sessionmaker[AsyncSession], specialist, now=_NOW):
    async with factory() as session:
        return await run_reminders_if_due(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            reminders_repo=SqlAlchemyRemindersRepo(session),
            specialists_repo=SqlAlchemySpecialistsRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            messages=_MESSAGES,
        )


async def test_due_pass_reminds_linked_client(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    to_send = await _run(session_factory, specialist)
    assert len(to_send) == 1
    item = to_send[0]
    assert item.chat_id == _CHAT
    assert "Петя" in item.text
    assert "10:00" in item.text
    # The day is marked done.
    async with session_factory() as session:
        reloaded = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert reloaded is not None
    assert reloaded.reminder_last_run_on == date(2026, 6, 15)


async def test_unlinked_client_skipped_but_day_marked(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory, chat_id=None)
    await _seed_appointment(session_factory, client_id)
    to_send = await _run(session_factory, specialist)
    assert to_send == []
    async with session_factory() as session:
        reloaded = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert reloaded is not None
    assert reloaded.reminder_last_run_on == date(2026, 6, 15)


async def test_empty_tomorrow_marks_day_only(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    await _seed_client(session_factory)  # linked, but no appointment tomorrow
    to_send = await _run(session_factory, specialist)
    assert to_send == []


async def test_series_repeat_is_reminded(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    async with session_factory() as session:
        schedule = await create_schedule(
            SqlAlchemyRecurringScheduleRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            comment=None,
            now=_NOW,
        )
        assert schedule.id is not None
        slot = await add_slot(
            SqlAlchemyRecurringSlotRepo(session),
            schedule_id=schedule.id,
            weekday=_TOMORROW.weekday(),  # lands on tomorrow
            time_hhmm="11:00",
            tz=_TZ,
            now=_NOW,
            start_date=_TOMORROW,
        )
    to_send = await _run(session_factory, specialist)
    assert len(to_send) == 1
    assert "11:00" in to_send[0].text
    # The journaled reminder for a virtual occurrence carries its slot_id.
    async with session_factory() as session:
        reminder = await SqlAlchemyRemindersRepo(session).get(to_send[0].reminder_id)
    assert reminder is not None
    assert reminder.slot_id == slot.id


async def test_not_due_disabled_sends_nothing(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    async with session_factory() as session:
        assert specialist.id is not None
        await SqlAlchemySpecialistsRepo(session).update_settings(
            specialist.id, {"reminder_enabled": False}
        )
    specialist.reminder_enabled = False
    assert await _run(session_factory, specialist) == []


async def test_idempotent_no_resend_for_same_occurrence(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    assert len(await _run(session_factory, specialist)) == 1
    # Reset the daily guard so the pass runs again, but the journal blocks re-sends.
    async with session_factory() as session:
        await SqlAlchemySpecialistsRepo(session).update_settings(
            _SP, {"reminder_last_run_on": None}
        )
    specialist.reminder_last_run_on = None
    assert await _run(session_factory, specialist) == []


# --- manual run (run_reminders_now) ------------------------------------------


async def _run_now(factory: async_sessionmaker[AsyncSession], specialist, now=_NOW):
    async with factory() as session:
        return await run_reminders_now(
            specialist,
            now,
            appointments_repo=SqlAlchemyAppointmentsRepo(session),
            reminders_repo=SqlAlchemyRemindersRepo(session),
            schedule_repo=SqlAlchemyRecurringScheduleRepo(session),
            slot_repo=SqlAlchemyRecurringSlotRepo(session),
            override_repo=SqlAlchemyRecurringSlotOverrideRepo(session),
            clients_repo=SqlAlchemyClientsRepo(session),
            messages=_MESSAGES,
        )


async def test_manual_run_sends_for_new_occurrence(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    to_send = await _run_now(session_factory, specialist)
    assert len(to_send) == 1
    assert to_send[0].chat_id == _CHAT


async def test_manual_run_does_not_resend_journalled_occurrence(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    assert len(await _run_now(session_factory, specialist)) == 1
    # The journal blocks a repeat send for the same occurrence (insert_pending False).
    assert await _run_now(session_factory, specialist) == []


async def test_manual_run_does_not_stamp_last_run_on(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    await _run_now(session_factory, specialist)
    async with session_factory() as session:
        reloaded = await SqlAlchemySpecialistsRepo(session).get(_SP)
    assert reloaded is not None
    assert reloaded.reminder_last_run_on is None


async def test_manual_run_works_when_reminders_disabled(
    session_factory: async_sessionmaker[AsyncSession],
):
    specialist = await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    async with session_factory() as session:
        assert specialist.id is not None
        await SqlAlchemySpecialistsRepo(session).update_settings(
            specialist.id, {"reminder_enabled": False}
        )
    specialist.reminder_enabled = False
    to_send = await _run_now(session_factory, specialist)
    assert len(to_send) == 1


# --- responses ---------------------------------------------------------------


async def _seed_reminder(
    factory: async_sessionmaker[AsyncSession], client_id: int
) -> int:
    async with factory() as session:
        repo = SqlAlchemyRemindersRepo(session)
        reminder = AppointmentReminder(
            id=None,
            specialist_id=_SP,
            client_id=client_id,
            starts_at=wall_to_utc(_TOMORROW, "10:00", _TZ),
            slot_id=None,
            origin_date=None,
            status=ReminderStatus.PENDING,
            sent_at=_NOW,
            responded_at=None,
        )
        await repo.insert_pending(reminder)
    assert reminder.id is not None
    return reminder.id


async def _respond(factory, reminder_id, *, chat_id=_CHAT, confirm):
    async with factory() as session:
        return await apply_reminder_response(
            SqlAlchemyRemindersRepo(session),
            SqlAlchemyClientsRepo(session),
            reminder_id=reminder_id,
            chat_id=chat_id,
            confirm=confirm,
            now=_NOW,
        )


async def test_confirm_sets_status_no_notify(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    result = await _respond(session_factory, reminder_id, confirm=True)
    assert result is not None
    assert result.notify_specialist is False
    async with session_factory() as session:
        loaded = await SqlAlchemyRemindersRepo(session).get(reminder_id)
    assert loaded is not None
    assert loaded.status is ReminderStatus.CONFIRMED


async def test_decline_notifies_specialist(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    result = await _respond(session_factory, reminder_id, confirm=False)
    assert result is not None
    assert result.notify_specialist is True


async def test_repeat_decline_does_not_renotify(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    await _respond(session_factory, reminder_id, confirm=False)
    result = await _respond(session_factory, reminder_id, confirm=False)
    assert result is not None
    assert result.notify_specialist is False


async def test_change_confirm_to_decline_notifies(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    await _respond(session_factory, reminder_id, confirm=True)
    result = await _respond(session_factory, reminder_id, confirm=False)
    assert result is not None
    assert result.notify_specialist is True


async def test_foreign_chat_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    reminder_id = await _seed_reminder(session_factory, client_id)
    assert (
        await _respond(session_factory, reminder_id, chat_id=999, confirm=True) is None
    )


async def test_unknown_reminder_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    assert await _respond(session_factory, 12345, confirm=True) is None


# --- status reads ------------------------------------------------------------


async def test_status_for_occurrence_and_for_appointments(
    session_factory: async_sessionmaker[AsyncSession],
):
    await _seed_specialist(session_factory)
    client_id = await _seed_client(session_factory)
    await _seed_appointment(session_factory, client_id)
    reminder_id = await _seed_reminder(session_factory, client_id)
    await _respond(session_factory, reminder_id, confirm=True)
    starts_at = wall_to_utc(_TOMORROW, "10:00", _TZ)
    async with session_factory() as session:
        single = await status_for_occurrence(
            SqlAlchemyRemindersRepo(session),
            specialist_id=_SP,
            client_id=client_id,
            starts_at=starts_at,
        )
        appts = await SqlAlchemyAppointmentsRepo(session).list_for_specialist_between(
            _SP,
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2027, 1, 1, tzinfo=UTC),
        )
        bulk = await statuses_for_appointments(
            SqlAlchemyRemindersRepo(session), specialist_id=_SP, appointments=appts
        )
    assert single is ReminderStatus.CONFIRMED
    assert bulk == {(client_id, starts_at): ReminderStatus.CONFIRMED}
