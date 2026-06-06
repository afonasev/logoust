from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.audit import AuditEvent
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessageStatus,
)
from src.infrastructure.scheduled_messages_repo import SqlAlchemyScheduledMessagesRepo

_SP = 1
_CLIENT = 2


def _message(
    *,
    target_key: str,
    due_at: datetime,
    client_id: int = _CLIENT,
    specialist_id: int = _SP,
    text: str = "привет",
) -> ScheduledClientMessage:
    return ScheduledClientMessage(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        chat_id=555,
        text=text,
        target_key=target_key,
        event=AuditEvent.NOTIFY_CREATED,
        due_at=due_at,
        status=ScheduledMessageStatus.QUEUED,
        created_at=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
        sent_at=None,
    )


_T1 = datetime(2026, 6, 6, 17, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 6, 18, 0, tzinfo=UTC)


async def test_enqueue_supersedes_same_target(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    first, superseded = await repo.enqueue_superseding(
        _message(target_key="appt:1", due_at=_T1)
    )
    assert superseded is None
    second, superseded2 = await repo.enqueue_superseding(
        _message(target_key="appt:1", due_at=_T2, text="новый")
    )
    assert superseded2 == _T1
    queued = await repo.list_queued_for_client(_SP, _CLIENT)
    assert [m.id for m in queued] == [second.id]
    assert queued[0].text == "новый"
    assert first.id != second.id


async def test_enqueue_different_targets_coexist(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    await repo.enqueue_superseding(_message(target_key="appt:1", due_at=_T1))
    await repo.enqueue_superseding(_message(target_key="appt:2", due_at=_T2))
    queued = await repo.list_queued_for_client(_SP, _CLIENT)
    assert {m.target_key for m in queued} == {"appt:1", "appt:2"}


async def test_list_due_only_queued_past_ascending(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    await repo.enqueue_superseding(_message(target_key="appt:2", due_at=_T2))
    await repo.enqueue_superseding(_message(target_key="appt:1", due_at=_T1))
    future = await repo.enqueue_superseding(
        _message(target_key="appt:3", due_at=datetime(2030, 1, 1, tzinfo=UTC))
    )
    due = await repo.list_due(datetime(2026, 6, 6, 19, 0, tzinfo=UTC))
    # Ascending by due_at; the far-future row is excluded.
    assert [m.due_at for m in due] == [_T1, _T2]
    assert future[0].id not in {m.id for m in due}


async def test_mark_sent_and_failed_set_status(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    sent, _ = await repo.enqueue_superseding(_message(target_key="appt:1", due_at=_T1))
    failed, _ = await repo.enqueue_superseding(
        _message(target_key="appt:2", due_at=_T2)
    )
    at = datetime(2026, 6, 6, 20, 0, tzinfo=UTC)
    assert sent.id is not None
    assert failed.id is not None
    await repo.mark_sent(sent.id, at)
    await repo.mark_failed(failed.id, at)
    # Both leave the queue; a later due pass would not pick them up.
    assert await repo.list_queued_for_client(_SP, _CLIENT) == []
    assert await repo.list_due(at) == []


async def test_cancel_owner_scoped(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    mine, _ = await repo.enqueue_superseding(_message(target_key="appt:1", due_at=_T1))
    assert mine.id is not None
    # A different specialist cannot cancel it.
    assert await repo.cancel(mine.id, 999) is False
    assert await repo.list_queued_for_client(_SP, _CLIENT) != []
    # The owner can.
    assert await repo.cancel(mine.id, _SP) is True
    assert await repo.list_queued_for_client(_SP, _CLIENT) == []
    # Cancelling an already-cancelled row is a no-op.
    assert await repo.cancel(mine.id, _SP) is False


async def test_cancel_missing_row(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    assert await repo.cancel(424242, _SP) is False


async def test_list_queued_for_client_filters(session: AsyncSession):
    repo = SqlAlchemyScheduledMessagesRepo(session)
    await repo.enqueue_superseding(
        _message(target_key="appt:1", due_at=_T1, client_id=_CLIENT)
    )
    await repo.enqueue_superseding(
        _message(target_key="appt:2", due_at=_T2, client_id=99)
    )
    queued = await repo.list_queued_for_client(_SP, _CLIENT)
    assert {m.client_id for m in queued} == {_CLIENT}
