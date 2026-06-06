from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.reminder import AppointmentReminder, ReminderStatus
from src.infrastructure.reminders_repo import SqlAlchemyRemindersRepo
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.invites import create_invite
from src.services.specialists import SettingField, update_setting

_SP = 1
_CLIENT = 7
_STARTS = datetime(2026, 6, 16, 5, 0, tzinfo=UTC)


def _reminder(**overrides: object) -> AppointmentReminder:
    base: dict[str, object] = {
        "id": None,
        "specialist_id": _SP,
        "client_id": _CLIENT,
        "starts_at": _STARTS,
        "slot_id": None,
        "origin_date": None,
        "status": ReminderStatus.PENDING,
        "sent_at": datetime(2026, 6, 15, 7, 0, tzinfo=UTC),
        "responded_at": None,
    }
    base.update(overrides)
    return AppointmentReminder(**base)  # type: ignore[arg-type]


async def test_insert_pending_sets_id_and_is_idempotent(session: AsyncSession):
    repo = SqlAlchemyRemindersRepo(session)
    reminder = _reminder()
    assert await repo.insert_pending(reminder) is True
    assert reminder.id is not None
    # Same occurrence again: ON CONFLICT DO NOTHING — no duplicate, returns False.
    again = _reminder()
    assert await repo.insert_pending(again) is False
    assert again.id is None


async def test_get_round_trips(session: AsyncSession):
    repo = SqlAlchemyRemindersRepo(session)
    reminder = _reminder(slot_id=3, origin_date=date(2026, 6, 16))
    await repo.insert_pending(reminder)
    assert reminder.id is not None
    loaded = await repo.get(reminder.id)
    assert loaded is not None
    assert loaded.slot_id == 3
    assert loaded.origin_date == date(2026, 6, 16)
    assert loaded.status is ReminderStatus.PENDING
    assert loaded.starts_at == _STARTS


async def test_get_missing_returns_none(session: AsyncSession):
    assert await SqlAlchemyRemindersRepo(session).get(999) is None


async def test_set_status_returns_previous(session: AsyncSession):
    repo = SqlAlchemyRemindersRepo(session)
    reminder = _reminder()
    await repo.insert_pending(reminder)
    assert reminder.id is not None
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    previous = await repo.set_status(reminder.id, ReminderStatus.CONFIRMED, now)
    assert previous is ReminderStatus.PENDING
    again = await repo.set_status(reminder.id, ReminderStatus.DECLINED, now)
    assert again is ReminderStatus.CONFIRMED
    loaded = await repo.get(reminder.id)
    assert loaded is not None
    assert loaded.status is ReminderStatus.DECLINED
    assert loaded.responded_at == now


async def test_statuses_for_day_filters_by_occurrence(session: AsyncSession):
    repo = SqlAlchemyRemindersRepo(session)
    other = datetime(2026, 6, 16, 6, 0, tzinfo=UTC)
    await repo.insert_pending(_reminder())
    confirmed = _reminder(client_id=8, starts_at=other)
    await repo.insert_pending(confirmed)
    assert confirmed.id is not None
    await repo.set_status(confirmed.id, ReminderStatus.CONFIRMED, other)
    statuses = await repo.statuses_for_day(
        _SP, [(_CLIENT, _STARTS), (8, other), (9, other)]
    )
    assert statuses == {
        (_CLIENT, _STARTS): ReminderStatus.PENDING,
        (8, other): ReminderStatus.CONFIRMED,
    }


async def test_statuses_for_day_empty_input(session: AsyncSession):
    assert await SqlAlchemyRemindersRepo(session).statuses_for_day(_SP, []) == {}


async def test_statuses_for_day_ignores_unrequested_occurrence(session: AsyncSession):
    repo = SqlAlchemyRemindersRepo(session)
    # Same client, a different time that the caller does not ask about.
    await repo.insert_pending(_reminder())
    other_time = datetime(2026, 6, 17, 5, 0, tzinfo=UTC)
    await repo.insert_pending(_reminder(starts_at=other_time))
    statuses = await repo.statuses_for_day(_SP, [(_CLIENT, _STARTS)])
    assert statuses == {(_CLIENT, _STARTS): ReminderStatus.PENDING}


async def test_list_reminder_candidates_only_enabled(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    enabled = await create_invite(repo)
    disabled = await create_invite(repo)
    assert disabled.id is not None
    await repo.update_settings(disabled.id, {"reminder_enabled": False})
    candidates = await repo.list_reminder_candidates()
    ids = {s.id for s in candidates}
    assert enabled.id in ids
    assert disabled.id not in ids


async def test_mark_reminder_run_persists(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)
    assert specialist.id is not None
    run_on = date(2026, 6, 15)
    await repo.mark_reminder_run(specialist.id, run_on)
    reloaded = await repo.get(specialist.id)
    assert reloaded is not None
    assert reloaded.reminder_last_run_on == run_on


async def test_reminder_time_setting_validates(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    specialist = await create_invite(repo)
    assert specialist.id is not None
    result = await update_setting(
        repo, specialist_id=specialist.id, field=SettingField.REMINDER_TIME, raw="9:30"
    )
    assert result.value == "updated"
    reloaded = await repo.get(specialist.id)
    assert reloaded is not None
    assert reloaded.reminder_time == "09:30"
