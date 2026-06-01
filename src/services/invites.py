from datetime import UTC, datetime
import enum
import logging
import secrets

from src.domain.specialist import (
    ChatIdConflictError,
    Specialist,
    SpecialistsRepo,
)

logger = logging.getLogger(__name__)


_TOKEN_BYTES = 16  # secrets.token_urlsafe(16) → 22-char URL-safe token


class ConsumeResult(enum.Enum):
    WELCOMED = "welcomed"
    ALREADY_WELCOMED = "already_welcomed"
    UNKNOWN_TOKEN = "unknown_token"  # noqa: S105 — enum value, not a credential


async def create_invite(repo: SpecialistsRepo) -> Specialist:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    specialist = Specialist(
        id=None,
        invite_token=token,
        telegram_chat_id=None,
        telegram_username=None,
        welcomed_at=None,
        created_at=datetime.now(UTC),
    )
    saved = await repo.add(specialist)
    logger.info(
        "specialist.invite_created",
        extra={"specialist_id": saved.id, "token_prefix": token[:6]},
    )
    return saved


async def consume_invite(
    repo: SpecialistsRepo,
    token: str,
    *,
    chat_id: int,
    username: str | None,
) -> ConsumeResult:
    specialist = await repo.find_by_token(token)
    if specialist is None:
        logger.info(
            "specialist.invite_unknown",
            extra={"token_prefix": token[:6]},
        )
        return ConsumeResult.UNKNOWN_TOKEN

    if specialist.welcomed_at is not None:
        logger.info(
            "specialist.invite_replayed",
            extra={
                "specialist_id": specialist.id,
                "token_prefix": token[:6],
            },
        )
        return ConsumeResult.ALREADY_WELCOMED

    if specialist.id is None:  # pragma: no cover — defensive; repo always assigns id
        msg = f"Specialist record for token {token[:6]}… has no id"
        raise RuntimeError(msg)
    try:
        await repo.mark_welcomed(
            specialist.id,
            telegram_chat_id=chat_id,
            telegram_username=username,
            welcomed_at=datetime.now(UTC),
        )
    except ChatIdConflictError:
        logger.warning(
            "specialist.invite_chat_conflict",
            extra={
                "specialist_id": specialist.id,
                "token_prefix": token[:6],
            },
        )
        return ConsumeResult.ALREADY_WELCOMED
    logger.info(
        "specialist.welcomed",
        extra={
            "specialist_id": specialist.id,
            "token_prefix": token[:6],
        },
    )
    return ConsumeResult.WELCOMED
