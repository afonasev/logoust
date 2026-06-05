from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Defaults for a freshly invited specialist; also the migration's server-defaults.
DEFAULT_TIMEZONE = "Asia/Yekaterinburg"
DEFAULT_DAY_START = "09:00"
DEFAULT_DAY_END = "20:00"
DEFAULT_SLOT_MINUTES = 60
# Canonical sorted weekday indices (Mon=0…Sun=6); default working days are Mon-Fri.
DEFAULT_WORKING_DAYS = "0,1,2,3,4"


class ChatIdConflictError(Exception):
    """Raised when binding a chat_id collides with another specialist."""


@dataclass(slots=True)
class Specialist:
    id: int | None
    invite_token: str
    telegram_chat_id: int | None
    telegram_username: str | None
    welcomed_at: datetime | None
    created_at: datetime
    # Schedule settings drive slot generation and wall-time ↔ UTC conversion.
    timezone: str = DEFAULT_TIMEZONE
    day_start: str = DEFAULT_DAY_START
    day_end: str = DEFAULT_DAY_END
    slot_minutes: int = DEFAULT_SLOT_MINUTES
    # Canonical sorted weekday indices (Mon=0…Sun=6), e.g. "0,1,2,3,4".
    working_days: str = DEFAULT_WORKING_DAYS


class SpecialistsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(self, specialist: Specialist) -> Specialist: ...  # pragma: no cover

    async def find_by_token(  # pragma: no cover
        self, token: str
    ) -> Specialist | None: ...

    async def find_by_chat_id(  # pragma: no cover
        self, chat_id: int
    ) -> Specialist | None: ...

    async def mark_welcomed(  # pragma: no cover
        self,
        specialist_id: int,
        *,
        telegram_chat_id: int,
        telegram_username: str | None,
        welcomed_at: datetime,
    ) -> None: ...

    async def get(  # pragma: no cover
        self, specialist_id: int
    ) -> Specialist | None: ...

    async def update_settings(  # pragma: no cover
        self, specialist_id: int, fields: Mapping[str, object]
    ) -> Specialist | None: ...
