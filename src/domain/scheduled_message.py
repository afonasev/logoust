"""Domain entity and repository protocol for deferred client notifications.

Pure Python — no SQLAlchemy or aiogram. A *scheduled client message* is a queued
outgoing notification to a client: a snapshot of the approved preview text plus the
moment it should be delivered. A background pass sends every row whose `due_at` has
arrived (see design.md, decisions 1-5). The `target_key` is a time-independent
identity of the appointment/series so re-deferring the same target supersedes the
previous row instead of piling up (decision 3).
"""

from dataclasses import dataclass
from datetime import date, datetime
import enum
from typing import Protocol

from src.domain.audit import AuditEvent


class ScheduledMessageStatus(enum.Enum):
    QUEUED = "queued"  # awaiting delivery at due_at
    SENT = "sent"  # delivered to the client
    FAILED = "failed"  # Telegram refused delivery (blocked / chat gone)
    CANCELLED = "cancelled"  # superseded or cancelled from the client card


@dataclass(slots=True)
class ScheduledClientMessage:
    id: int | None
    specialist_id: int
    client_id: int
    chat_id: int  # snapshot of the client's chat at enqueue time
    text: str  # snapshot of the approved preview text
    target_key: str  # stable appointment/series identity (see appointment_target_key)
    # Audit event the delivery journals as — the notify event captured at enqueue
    # time, so the outbox pass can record the message without re-deriving it.
    event: AuditEvent
    due_at: datetime  # aware UTC; when to deliver
    status: ScheduledMessageStatus
    created_at: datetime
    sent_at: datetime | None  # when delivery was attempted (sent or failed)


def appointment_target_key(appointment_id: int) -> str:
    """Stable supersede key for a one-off appointment (survives reschedule)."""
    return f"appt:{appointment_id}"


def schedule_target_key(schedule_id: int) -> str:
    """Stable supersede key for a whole recurring schedule."""
    return f"schedule:{schedule_id}"


def slot_date_target_key(slot_id: int, origin_date: date) -> str:
    """Stable supersede key for a single occurrence of a slot."""
    return f"slot:{slot_id}:{origin_date.isoformat()}"


def is_message_due(message: ScheduledClientMessage, now: datetime) -> bool:
    """Whether the outbox pass should deliver `message` at `now`.

    True for a queued row whose delivery moment has arrived; an `<=` threshold gives
    catch-up after downtime (any overdue queued row goes out on the next tick).
    """
    return message.status is ScheduledMessageStatus.QUEUED and message.due_at <= now


class ScheduledMessagesRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def enqueue_superseding(  # pragma: no cover
        self, message: ScheduledClientMessage
    ) -> tuple[ScheduledClientMessage, datetime | None]: ...

    async def list_due(  # pragma: no cover
        self, now: datetime
    ) -> list[ScheduledClientMessage]: ...

    async def mark_sent(  # pragma: no cover
        self, message_id: int, at: datetime
    ) -> None: ...

    async def mark_failed(  # pragma: no cover
        self, message_id: int, at: datetime
    ) -> None: ...

    async def cancel(  # pragma: no cover
        self, message_id: int, specialist_id: int
    ) -> bool: ...

    async def list_queued_for_client(  # pragma: no cover
        self, specialist_id: int, client_id: int
    ) -> list[ScheduledClientMessage]: ...
