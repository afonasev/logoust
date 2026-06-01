from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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


class SpecialistsRepo(Protocol):
    # Protocol method bodies are unimplemented placeholders by design.
    async def add(self, specialist: Specialist) -> Specialist: ...  # pragma: no cover

    async def find_by_token(  # pragma: no cover
        self, token: str
    ) -> Specialist | None: ...

    async def mark_welcomed(  # pragma: no cover
        self,
        specialist_id: int,
        *,
        telegram_chat_id: int,
        telegram_username: str | None,
        welcomed_at: datetime,
    ) -> None: ...
