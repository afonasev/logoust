from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import AuditEvent, AuditKind, DeliveryStatus
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.services.audit import list_audit, record_action, record_message

_SP = 1


async def test_record_action_writes_action_row(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    entry = await record_action(
        repo, specialist_id=_SP, event=AuditEvent.CLIENT_ARCHIVED, client_id=3
    )
    assert entry.kind is AuditKind.ACTION
    assert entry.event is AuditEvent.CLIENT_ARCHIVED
    assert entry.text is None


async def test_record_message_writes_message_row(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    entry = await record_message(
        repo,
        specialist_id=_SP,
        client_id=3,
        event=AuditEvent.WELCOME,
        text="Здравствуйте!",
        status=DeliveryStatus.FAILED,
        error="bot blocked",
    )
    assert entry.kind is AuditKind.MESSAGE
    assert entry.status is DeliveryStatus.FAILED
    assert entry.error == "bot blocked"


async def test_list_audit_pagination_flags(session: AsyncSession):
    repo = SqlAlchemyAuditRepo(session)
    for _ in range(5):
        await record_action(repo, specialist_id=_SP, event=AuditEvent.APPT_CREATED)

    first = await list_audit(repo, specialist_id=_SP, page=0, page_size=2)
    assert len(first.entries) == 2
    assert first.has_prev is False
    assert first.has_next is True

    last = await list_audit(repo, specialist_id=_SP, page=2, page_size=2)
    assert len(last.entries) == 1
    assert last.has_prev is True
    assert last.has_next is False


async def test_list_audit_empty(session: AsyncSession):
    page = await list_audit(
        SqlAlchemyAuditRepo(session), specialist_id=_SP, page=0, page_size=10
    )
    assert page.entries == []
    assert page.has_prev is False
    assert page.has_next is False
