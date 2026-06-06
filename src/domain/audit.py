from dataclasses import dataclass
from datetime import datetime
import enum
from typing import Protocol


class AuditKind(enum.Enum):
    """Природа строки журнала: исходящее клиенту vs действие специалиста."""

    MESSAGE = "message"
    ACTION = "action"


class AuditEvent(enum.Enum):
    """Закрытый список журналируемых событий (см. design.md, решение 4)."""

    # message-события (отправки клиенту); часть приходит из appointment-notify.
    NOTIFY_CREATED = "notify_created"
    NOTIFY_RESCHEDULED = "notify_rescheduled"
    NOTIFY_CANCELLED = "notify_cancelled"
    WELCOME = "welcome"
    REMINDER = "reminder"
    # action-события (ключевые действия специалиста).
    CLIENT_CREATED = "client_created"
    CLIENT_ARCHIVED = "client_archived"
    CLIENT_RESTORED = "client_restored"
    APPT_CREATED = "appt_created"
    APPT_RESCHEDULED = "appt_rescheduled"
    APPT_DELETED = "appt_deleted"


class DeliveryStatus(enum.Enum):
    """Статус доставки message-строки."""

    SENT = "sent"
    FAILED = "failed"


@dataclass(slots=True)
class AuditEntry:
    id: int | None
    specialist_id: int
    created_at: datetime
    kind: AuditKind
    event: AuditEvent
    client_id: int | None = None
    # text/status/error заполнены только для kind=message (см. spec).
    text: str | None = None
    status: DeliveryStatus | None = None
    error: str | None = None


class AuditRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def record(  # noqa: PLR0913  # pragma: no cover
        self,
        *,
        specialist_id: int,
        kind: AuditKind,
        event: AuditEvent,
        client_id: int | None = None,
        text: str | None = None,
        status: DeliveryStatus | None = None,
        error: str | None = None,
    ) -> AuditEntry: ...

    async def list_for_specialist(  # pragma: no cover
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[AuditEntry]: ...

    async def count_for_specialist(  # pragma: no cover
        self, specialist_id: int
    ) -> int: ...
