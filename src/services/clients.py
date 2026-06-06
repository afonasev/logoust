from dataclasses import dataclass
from datetime import UTC, datetime
import enum
import logging
import secrets

from src.domain.audit import AuditEvent, AuditRepo
from src.domain.client import (
    Client,
    ClientsRepo,
    ClientStatus,
    ClientValidationError,
    ValidationReason,
    normalize_phone,
    normalize_telegram,
)
from src.services.audit import record_action

logger = logging.getLogger(__name__)

_TOKEN_BYTES = 16  # secrets.token_urlsafe(16) → 22-char URL-safe token
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
class ClientsPage:
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


async def add_client(
    repo: ClientsRepo, data: NewClient, *, audit: AuditRepo | None = None
) -> Client:
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
    if audit is not None:
        await record_action(
            audit,
            specialist_id=data.specialist_id,
            event=AuditEvent.CLIENT_CREATED,
            client_id=saved.id,
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
    repo: ClientsRepo,
    *,
    client_id: int,
    specialist_id: int,
    audit: AuditRepo | None = None,
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
    if audit is not None:
        await record_action(
            audit,
            specialist_id=specialist_id,
            event=AuditEvent.CLIENT_ARCHIVED,
            client_id=client_id,
        )
    return True


async def restore_client(
    repo: ClientsRepo,
    *,
    client_id: int,
    specialist_id: int,
    audit: AuditRepo | None = None,
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
    if audit is not None:
        await record_action(
            audit,
            specialist_id=specialist_id,
            event=AuditEvent.CLIENT_RESTORED,
            client_id=client_id,
        )
    return True


async def create_client_invite(
    repo: ClientsRepo, *, client_id: int, specialist_id: int
) -> Client | None:
    """Лениво выдать клиенту invite_token (переиспользуя существующий).

    Возвращает обновлённую карточку, где задан `invite_token`, либо None, если
    карточка не принадлежит специалисту.
    """
    client = await repo.get_for_specialist(client_id, specialist_id)
    if client is None:
        return None
    if client.invite_token:
        return client  # ссылка стабильна — переиспользуем существующий токен
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    updated = await repo.set_invite_token(
        client_id, specialist_id, token, updated_at=datetime.now(UTC)
    )
    logger.info(
        "client.invite_created",
        extra={"specialist_id": specialist_id, "client_id": client_id},
    )
    return updated


async def link_client_by_token(
    repo: ClientsRepo, token: str, *, chat_id: int, username: str | None = None
) -> Client | None:
    """Привязать Telegram клиента по invite_token (idempotent rebind).

    `username` (если есть) автозаполняет пустой `contact_telegram`.
    Возвращает обновлённую карточку, либо None для неизвестного токена.
    """
    client = await repo.find_by_invite_token(token)
    if client is None or client.id is None:
        logger.info("client.link_unknown", extra={"token_prefix": token[:6]})
        return None
    now = datetime.now(UTC)
    updated = await repo.link_telegram(
        client.id,
        telegram_chat_id=chat_id,
        username=username,
        linked_at=now,
        updated_at=now,
    )
    logger.info(
        "client.linked",
        extra={"specialist_id": client.specialist_id, "client_id": client.id},
    )
    return updated


async def list_clients(
    repo: ClientsRepo, *, specialist_id: int, status: ClientStatus
) -> list[Client]:
    return await repo.list_by_status(specialist_id, status)


async def client_name_map(repo: ClientsRepo, *, specialist_id: int) -> dict[int, str]:
    """Map client_id → child_name across both statuses for schedule rendering."""
    active = await repo.list_by_status(specialist_id, ClientStatus.ACTIVE)
    archived = await repo.list_by_status(specialist_id, ClientStatus.ARCHIVED)
    return {c.id: c.child_name for c in (*active, *archived) if c.id is not None}


def _to_page(rows: list[Client], *, page: int, page_size: int) -> ClientsPage:
    # Fetch one extra row to detect a next page without a separate COUNT query.
    return ClientsPage(
        clients=rows[:page_size],
        page=page,
        has_prev=page > 0,
        has_next=len(rows) > page_size,
    )


async def list_active_page(
    repo: ClientsRepo, *, specialist_id: int, page: int, page_size: int
) -> ClientsPage:
    rows = await repo.list_active(
        specialist_id, limit=page_size + 1, offset=page * page_size
    )
    return _to_page(rows, page=page, page_size=page_size)


async def list_archived_page(
    repo: ClientsRepo, *, specialist_id: int, page: int, page_size: int
) -> ClientsPage:
    rows = await repo.list_archived(
        specialist_id, limit=page_size + 1, offset=page * page_size
    )
    return _to_page(rows, page=page, page_size=page_size)
