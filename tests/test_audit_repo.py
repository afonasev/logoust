from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.infrastructure.audit_repo import AuditLogORM, SqlAlchemyAuditRepo, to_domain

_SP = 1


async def test_record_action_returns_entry(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    entry = await repo.record(
        specialist_id=_SP,
        kind=AuditKind.ACTION,
        event=AuditEvent.CLIENT_CREATED,
        client_id=7,
    )
    assert entry.id is not None
    assert entry.kind is AuditKind.ACTION
    assert entry.event is AuditEvent.CLIENT_CREATED
    assert entry.client_id == 7
    assert entry.text is None
    assert entry.status is None


async def test_record_message_persists_text_and_status(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    entry = await repo.record(
        specialist_id=_SP,
        kind=AuditKind.MESSAGE,
        event=AuditEvent.NOTIFY_CREATED,
        client_id=7,
        text="Запись создана на 10:00",
        status=DeliveryStatus.SENT,
    )
    assert entry.status is DeliveryStatus.SENT
    assert entry.text == "Запись создана на 10:00"
    assert entry.error is None


async def test_count_for_specialist(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    assert await repo.count_for_specialist(_SP) == 0
    for _ in range(3):
        await repo.record(
            specialist_id=_SP, kind=AuditKind.ACTION, event=AuditEvent.APPT_CREATED
        )
    assert await repo.count_for_specialist(_SP) == 3


async def test_list_isolated_by_specialist(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    await repo.record(
        specialist_id=_SP, kind=AuditKind.ACTION, event=AuditEvent.WELCOME
    )
    await repo.record(specialist_id=99, kind=AuditKind.ACTION, event=AuditEvent.WELCOME)
    mine = await repo.list_for_specialist(_SP, limit=10, offset=0)
    assert [e.specialist_id for e in mine] == [_SP]
    assert await repo.count_for_specialist(_SP) == 1


async def test_list_sorted_newest_first(session: AsyncSession):
    # Explicit created_at out of order; the feed must come back newest-first.
    for day in (2, 1, 3):
        session.add(
            AuditLogORM(
                specialist_id=_SP,
                kind=AuditKind.ACTION.value,
                event=AuditEvent.APPT_CREATED.value,
                created_at=datetime(2026, 6, day, tzinfo=UTC),
            )
        )
    await session.commit()
    rows = await SqlAlchemyAuditRepo(session).list_for_specialist(
        _SP, limit=10, offset=0
    )
    assert [e.created_at.day for e in rows] == [3, 2, 1]


async def test_list_pagination(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    for _ in range(5):
        await repo.record(
            specialist_id=_SP, kind=AuditKind.ACTION, event=AuditEvent.APPT_CREATED
        )
    first = await repo.list_for_specialist(_SP, limit=2, offset=0)
    third = await repo.list_for_specialist(_SP, limit=2, offset=4)
    assert len(first) == 2
    assert len(third) == 1


def test_to_domain_and_repr():
    orm = AuditLogORM(
        id=3,
        specialist_id=_SP,
        kind=AuditKind.MESSAGE.value,
        event=AuditEvent.NOTIFY_CREATED.value,
        client_id=5,
        text="hi",
        status=DeliveryStatus.FAILED.value,
        error="blocked",
        created_at=datetime(2026, 6, 6, tzinfo=UTC),
    )
    domain = to_domain(orm)
    assert domain.status is DeliveryStatus.FAILED
    assert domain.error == "blocked"
    assert "event=notify_created" in repr(orm)
