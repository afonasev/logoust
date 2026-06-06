from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import AuditEvent
from src.domain.scheduled_message import ScheduledMessageStatus
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo
from src.services.scheduled_messages import (
    cancel_deferred,
    collect_due,
    enqueue_deferred,
    list_queued_for_client,
)

_SP = 1
_CLIENT = 2
_NOW = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
_DUE1 = datetime(2026, 6, 6, 15, 0, tzinfo=UTC)
_DUE2 = datetime(2026, 6, 6, 16, 0, tzinfo=UTC)


async def _enqueue(
    session: AsyncSession, *, target_key: str, due_at: datetime, text="привет"
):
    return await enqueue_deferred(
        SqlAlchemyScheduledMessagesRepo(session),
        specialist_id=_SP,
        client_id=_CLIENT,
        chat_id=555,
        text=text,
        target_key=target_key,
        event=AuditEvent.NOTIFY_CREATED,
        due_at=due_at,
        now=_NOW,
    )


async def test_enqueue_without_supersede(session: AsyncSession):
    result = await _enqueue(session, target_key="appt:1", due_at=_DUE1)
    assert result.superseded_due_at is None
    assert result.message.status is ScheduledMessageStatus.QUEUED
    assert result.message.id is not None


async def test_enqueue_with_supersede(session: AsyncSession):
    await _enqueue(session, target_key="appt:1", due_at=_DUE1)
    result = await _enqueue(session, target_key="appt:1", due_at=_DUE2, text="новый")
    assert result.superseded_due_at == _DUE1
    queued = await list_queued_for_client(
        SqlAlchemyScheduledMessagesRepo(session),
        specialist_id=_SP,
        client_id=_CLIENT,
    )
    assert [m.text for m in queued] == ["новый"]


async def test_collect_due(session: AsyncSession):
    await _enqueue(session, target_key="appt:1", due_at=_DUE1)
    await _enqueue(
        session, target_key="appt:2", due_at=datetime(2030, 1, 1, tzinfo=UTC)
    )
    due = await collect_due(
        SqlAlchemyScheduledMessagesRepo(session),
        datetime(2026, 6, 6, 17, 0, tzinfo=UTC),
    )
    assert [m.target_key for m in due] == ["appt:1"]


async def test_cancel_deferred(session: AsyncSession):
    result = await _enqueue(session, target_key="appt:1", due_at=_DUE1)
    assert result.message.id is not None
    cancelled = await cancel_deferred(
        SqlAlchemyScheduledMessagesRepo(session),
        message_id=result.message.id,
        specialist_id=_SP,
    )
    assert cancelled is True
    queued = await list_queued_for_client(
        SqlAlchemyScheduledMessagesRepo(session),
        specialist_id=_SP,
        client_id=_CLIENT,
    )
    assert queued == []


async def test_cancel_deferred_foreign_noop(session: AsyncSession):
    result = await _enqueue(session, target_key="appt:1", due_at=_DUE1)
    assert result.message.id is not None
    cancelled = await cancel_deferred(
        SqlAlchemyScheduledMessagesRepo(session),
        message_id=result.message.id,
        specialist_id=999,
    )
    assert cancelled is False
