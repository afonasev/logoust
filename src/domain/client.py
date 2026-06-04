from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import enum
import re
from typing import Protocol


class ClientStatus(enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ValidationReason(enum.Enum):
    EMPTY_CHILD_NAME = "empty_child_name"
    EMPTY_CONTACT_NAME = "empty_contact_name"
    NO_CONTACT_CHANNEL = "no_contact_channel"


class ClientValidationError(Exception):
    """Raised when a new client fails the required-minimum validation."""

    def __init__(self, reason: ValidationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(slots=True)
class Client:
    id: int | None
    specialist_id: int
    child_name: str
    contact_name: str
    contact_phone: str | None
    contact_telegram: str | None
    extra_contacts: str | None
    note: str | None
    status: ClientStatus
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


_NON_DIGITS = re.compile(r"[^0-9]")
_FULL_NUMBER_LEN = 11  # код страны + 10 значащих цифр
_NATIONAL_LEN = 10


def normalize_phone(raw: str) -> str:
    """Canonicalise a phone to +7XXXXXXXXXX.

    Accepts input prefixed with 8, +7, 7 or no country code, with any formatting.
    When the input does not reduce to a Russian number (10 significant digits),
    returns the trimmed original instead — we never block the specialist on a
    phone we cannot parse (see design.md, decision 4).
    """
    digits = _NON_DIGITS.sub("", raw)
    national = _to_national(digits)
    if national is None:
        return raw.strip()
    return f"+7{national}"


def _to_national(digits: str) -> str | None:
    if len(digits) == _FULL_NUMBER_LEN and digits[0] in {"7", "8"}:
        return digits[1:]
    if len(digits) == _NATIONAL_LEN:
        return digits
    return None


def normalize_telegram(raw: str) -> str:
    """Store a Telegram username without a leading @ (format is not validated)."""
    stripped = raw.strip()
    return stripped.removeprefix("@")


class ClientsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(self, client: Client) -> Client: ...  # pragma: no cover

    async def get_for_specialist(  # pragma: no cover
        self, client_id: int, specialist_id: int
    ) -> Client | None: ...

    async def list_by_status(  # pragma: no cover
        self, specialist_id: int, status: ClientStatus
    ) -> list[Client]: ...

    async def list_archived(  # pragma: no cover
        self, specialist_id: int, *, limit: int, offset: int
    ) -> list[Client]: ...

    async def update_fields(  # pragma: no cover
        self,
        client_id: int,
        specialist_id: int,
        fields: Mapping[str, object],
        *,
        updated_at: datetime,
    ) -> Client | None: ...

    async def set_status(  # pragma: no cover
        self,
        client_id: int,
        specialist_id: int,
        status: ClientStatus,
        *,
        archived_at: datetime | None,
        updated_at: datetime,
    ) -> Client | None: ...
