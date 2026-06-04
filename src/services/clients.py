from dataclasses import dataclass
from datetime import UTC, datetime
import enum
import logging

from src.domain.client import (
    Client,
    ClientsRepo,
    ClientStatus,
    ClientValidationError,
    ValidationReason,
    normalize_phone,
    normalize_telegram,
)

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = frozenset({"child_name", "contact_name"})
_EDITABLE_FIELDS = frozenset(
    {
        "child_name",
        "contact_name",
        "contact_phone",
        "contact_telegram",
        "extra_contacts",
        "note",
    }
)


class EditResult(enum.Enum):
    UPDATED = "updated"
    EMPTY_REQUIRED = "empty_required"
    NOT_FOUND = "not_found"


@dataclass(slots=True)
class ArchivePage:
    clients: list[Client]
    page: int
    has_prev: bool
    has_next: bool


@dataclass(slots=True)
class NewClient:
    specialist_id: int
    child_name: str
    contact_name: str
    contact_phone: str | None = None
    contact_telegram: str | None = None
    extra_contacts: str | None = None
    note: str | None = None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def add_client(repo: ClientsRepo, data: NewClient) -> Client:
    child = _clean(data.child_name)
    contact = _clean(data.contact_name)
    if child is None:
        raise ClientValidationError(ValidationReason.EMPTY_CHILD_NAME)
    if contact is None:
        raise ClientValidationError(ValidationReason.EMPTY_CONTACT_NAME)

    raw_phone = _clean(data.contact_phone)
    raw_telegram = _clean(data.contact_telegram)
    phone = normalize_phone(raw_phone) if raw_phone else None
    telegram = normalize_telegram(raw_telegram) if raw_telegram else None
    if phone is None and telegram is None:
        raise ClientValidationError(ValidationReason.NO_CONTACT_CHANNEL)

    now = datetime.now(UTC)
    client = Client(
        id=None,
        specialist_id=data.specialist_id,
        child_name=child,
        contact_name=contact,
        contact_phone=phone,
        contact_telegram=telegram,
        extra_contacts=_clean(data.extra_contacts),
        note=_clean(data.note),
        status=ClientStatus.ACTIVE,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    saved = await repo.add(client)
    logger.info(
        "client.created",
        extra={"specialist_id": data.specialist_id, "client_id": saved.id},
    )
    return saved


def _prepare_value(field: str, cleaned: str) -> str | None:
    if field == "contact_phone":
        return normalize_phone(cleaned) if cleaned else None
    if field == "contact_telegram":
        return normalize_telegram(cleaned) if cleaned else None
    if field in _REQUIRED_FIELDS:
        return cleaned
    return cleaned or None


async def edit_client_field(
    repo: ClientsRepo,
    *,
    client_id: int,
    specialist_id: int,
    field: str,
    value: str,
) -> EditResult:
    if field not in _EDITABLE_FIELDS:
        msg = f"Field {field!r} is not editable"
        raise ValueError(msg)

    cleaned = value.strip()
    if field in _REQUIRED_FIELDS and not cleaned:
        return EditResult.EMPTY_REQUIRED

    updated = await repo.update_fields(
        client_id,
        specialist_id,
        {field: _prepare_value(field, cleaned)},
        updated_at=datetime.now(UTC),
    )
    if updated is None:
        return EditResult.NOT_FOUND

    logger.info(
        "client.field_updated",
        extra={
            "specialist_id": specialist_id,
            "client_id": client_id,
            "field": field,
        },
    )
    return EditResult.UPDATED


async def archive_client(
    repo: ClientsRepo, *, client_id: int, specialist_id: int
) -> bool:
    now = datetime.now(UTC)
    client = await repo.set_status(
        client_id,
        specialist_id,
        ClientStatus.ARCHIVED,
        archived_at=now,
        updated_at=now,
    )
    if client is None:
        return False
    logger.info(
        "client.archived",
        extra={"specialist_id": specialist_id, "client_id": client_id},
    )
    return True


async def restore_client(
    repo: ClientsRepo, *, client_id: int, specialist_id: int
) -> bool:
    now = datetime.now(UTC)
    client = await repo.set_status(
        client_id,
        specialist_id,
        ClientStatus.ACTIVE,
        archived_at=None,
        updated_at=now,
    )
    if client is None:
        return False
    logger.info(
        "client.restored",
        extra={"specialist_id": specialist_id, "client_id": client_id},
    )
    return True


async def list_clients(
    repo: ClientsRepo, *, specialist_id: int, status: ClientStatus
) -> list[Client]:
    return await repo.list_by_status(specialist_id, status)


async def list_archived_page(
    repo: ClientsRepo, *, specialist_id: int, page: int, page_size: int
) -> ArchivePage:
    # Fetch one extra row to detect a next page without a separate COUNT query.
    rows = await repo.list_archived(
        specialist_id, limit=page_size + 1, offset=page * page_size
    )
    return ArchivePage(
        clients=rows[:page_size],
        page=page,
        has_prev=page > 0,
        has_next=len(rows) > page_size,
    )
