from dataclasses import dataclass
import logging

from src.domain.audit import (
    AuditEntry,
    AuditEvent,
    AuditKind,
    AuditRepo,
    DeliveryStatus,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuditPage:
    entries: list[AuditEntry]
    page: int
    has_prev: bool
    has_next: bool


async def record_action(
    repo: AuditRepo,
    *,
    specialist_id: int,
    event: AuditEvent,
    client_id: int | None = None,
) -> AuditEntry:
    """Записать action-строку (ключевое действие специалиста, без текста/статуса)."""
    entry = await repo.record(
        specialist_id=specialist_id,
        kind=AuditKind.ACTION,
        event=event,
        client_id=client_id,
    )
    logger.info(
        "audit.recorded",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "kind": AuditKind.ACTION.value,
            "event": event.value,
        },
    )
    return entry


async def record_message(  # noqa: PLR0913
    repo: AuditRepo,
    *,
    specialist_id: int,
    client_id: int,
    event: AuditEvent,
    text: str,
    status: DeliveryStatus,
    error: str | None = None,
) -> AuditEntry:
    """Записать message-строку: исходящее клиенту, текст и статус доставки."""
    entry = await repo.record(
        specialist_id=specialist_id,
        kind=AuditKind.MESSAGE,
        event=event,
        client_id=client_id,
        text=text,
        status=status,
        error=error,
    )
    logger.info(
        "audit.recorded",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "kind": AuditKind.MESSAGE.value,
            "event": event.value,
            "status": status.value,
        },
    )
    return entry


async def list_audit(
    repo: AuditRepo, *, specialist_id: int, page: int, page_size: int
) -> AuditPage:
    entries = await repo.list_for_specialist(
        specialist_id, limit=page_size, offset=page * page_size
    )
    total = await repo.count_for_specialist(specialist_id)
    return AuditPage(
        entries=entries,
        page=page,
        has_prev=page > 0,
        has_next=(page + 1) * page_size < total,
    )
