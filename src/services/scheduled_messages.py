"""Use-cases for deferred client notifications: enqueue, collect, cancel, list.

Pure of aiogram — the outbox pass in `src/bot/scheduler.py` injects the repo and
does the actual `bot.send_message`. Enqueuing supersedes any prior queued row for
the same target (see design.md, decision 3) and reports the replaced send time so
the bot can tell the specialist "previous send replaced".
"""

from dataclasses import dataclass
from datetime import datetime
import logging

from src.domain.audit import AuditEvent
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessagesRepo,
    ScheduledMessageStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Outcome of enqueuing a deferred notification."""

    message: ScheduledClientMessage
    # Due time of the queued row this one replaced, or None when nothing was there.
    superseded_due_at: datetime | None


async def enqueue_deferred(  # noqa: PLR0913
    repo: ScheduledMessagesRepo,
    *,
    specialist_id: int,
    client_id: int,
    chat_id: int,
    text: str,
    target_key: str,
    event: AuditEvent,
    due_at: datetime,
    now: datetime,
) -> EnqueueResult:
    """Queue an approved notification snapshot for delivery at `due_at`."""
    message = ScheduledClientMessage(
        id=None,
        specialist_id=specialist_id,
        client_id=client_id,
        chat_id=chat_id,
        text=text,
        target_key=target_key,
        event=event,
        due_at=due_at,
        status=ScheduledMessageStatus.QUEUED,
        created_at=now,
        sent_at=None,
    )
    inserted, superseded_due_at = await repo.enqueue_superseding(message)
    logger.info(
        "appointment.notify_deferred",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "superseded": superseded_due_at is not None,
        },
    )
    return EnqueueResult(message=inserted, superseded_due_at=superseded_due_at)


async def collect_due(
    repo: ScheduledMessagesRepo, now: datetime
) -> list[ScheduledClientMessage]:
    """Queued notifications whose delivery moment has arrived, due_at-ascending."""
    return await repo.list_due(now)


async def cancel_deferred(
    repo: ScheduledMessagesRepo, *, message_id: int, specialist_id: int
) -> bool:
    """Cancel a queued notification; owner-scoped. True when one was cancelled."""
    cancelled = await repo.cancel(message_id, specialist_id)
    if cancelled:
        logger.info(
            "appointment.notify_cancelled_deferred",
            extra={"specialist_id": specialist_id, "message_id": message_id},
        )
    return cancelled


async def list_queued_for_client(
    repo: ScheduledMessagesRepo, *, specialist_id: int, client_id: int
) -> list[ScheduledClientMessage]:
    """Queued notifications for one client, due_at-ascending — for the card block."""
    return await repo.list_queued_for_client(specialist_id, client_id)
