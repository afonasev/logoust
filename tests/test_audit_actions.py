from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import AuditEvent
from src.infrastructure.appointments_repo import SqlAlchemyAppointmentsRepo
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.infrastructure.clients_repo import SqlAlchemyClientsRepo
from src.services.appointments import (
    create_appointment,
    delete_appointment,
    reschedule_appointment,
)
from src.services.clients import (
    NewClient,
    add_client,
    archive_client,
    edit_client_field,
    restore_client,
)

_SP = 1
_TZ = "Asia/Yekaterinburg"
_FUTURE = date(2099, 6, 1)


def _new() -> NewClient:
    return NewClient(
        specialist_id=_SP,
        child_name="Маша",
        contact_name="Мама",
        contact_phone="+70000000000",
    )


async def _events(session: AsyncSession) -> list[AuditEvent]:
    rows = await SqlAlchemyAuditRepo(session).list_for_specialist(
        _SP, limit=50, offset=0
    )
    return [e.event for e in rows]


async def test_client_lifecycle_records_actions(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    audit = SqlAlchemyAuditRepo(session)
    client = await add_client(repo, _new(), audit=audit)
    assert client.id is not None
    await archive_client(repo, client_id=client.id, specialist_id=_SP, audit=audit)
    await restore_client(repo, client_id=client.id, specialist_id=_SP, audit=audit)
    events = await _events(session)
    assert set(events) == {
        AuditEvent.CLIENT_CREATED,
        AuditEvent.CLIENT_ARCHIVED,
        AuditEvent.CLIENT_RESTORED,
    }


async def test_non_logged_action_writes_no_row(session: AsyncSession):
    repo = SqlAlchemyClientsRepo(session)
    audit = SqlAlchemyAuditRepo(session)
    client = await add_client(repo, _new(), audit=audit)
    assert client.id is not None
    # Editing a field is deliberately not journalled (no audit parameter).
    await edit_client_field(
        repo,
        client_id=client.id,
        specialist_id=_SP,
        field="note",
        value="заметка",
    )
    events = await _events(session)
    assert events == [AuditEvent.CLIENT_CREATED]  # only the creation row


async def test_appointment_lifecycle_records_actions(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    audit = SqlAlchemyAuditRepo(session)
    now = datetime.now(UTC)
    appt = await create_appointment(
        repo,
        specialist_id=_SP,
        client_id=5,
        day=_FUTURE,
        hhmm="10:00",
        comment=None,
        tz=_TZ,
        now=now,
        audit=audit,
    )
    assert appt.id is not None
    await reschedule_appointment(
        repo,
        appointment_id=appt.id,
        specialist_id=_SP,
        day=_FUTURE,
        hhmm="11:00",
        tz=_TZ,
        now=now,
        audit=audit,
    )
    await delete_appointment(
        repo,
        appointment_id=appt.id,
        specialist_id=_SP,
        audit=audit,
        client_id=appt.client_id,
    )
    assert await _events(session) == [
        AuditEvent.APPT_DELETED,
        AuditEvent.APPT_RESCHEDULED,
        AuditEvent.APPT_CREATED,
    ]


async def test_delete_missing_appointment_writes_no_row(session: AsyncSession):
    repo = SqlAlchemyAppointmentsRepo(session)
    audit = SqlAlchemyAuditRepo(session)
    deleted = await delete_appointment(
        repo, appointment_id=404, specialist_id=_SP, audit=audit, client_id=1
    )
    assert deleted is False
    assert await _events(session) == []
