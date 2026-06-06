"""Use-cases for client message templates: resolve, override, reset.

`resolve_template` is the single entry point every client-message send site uses
to obtain the template text: the specialist's override if present, otherwise the
default the caller passes in (read from messages.toml in the bot layer). The
default is passed in — not looked up here — so this layer stays free of any
dependency on the bot's message catalog.
"""

import logging

from src.domain.message_template import (
    MessageTemplatesRepo,
    Violation,
    validate_template,
)

logger = logging.getLogger(__name__)


async def resolve_template(
    repo: MessageTemplatesRepo,
    *,
    specialist_id: int,
    key: str,
    default: str,
) -> str:
    """The specialist's override for `key`, or `default` when none is stored."""
    override = await repo.get(specialist_id, key)
    return override.body if override is not None else default


async def save_template_override(
    repo: MessageTemplatesRepo,
    *,
    specialist_id: int,
    key: str,
    body: str,
) -> list[Violation]:
    """Validate then store an override; return violations (empty ⇒ saved).

    The override is written only when validation passes, so a text that breaks an
    invariant (missing required / disallowed placeholder) never reaches a client.
    """
    violations = validate_template(key, body)
    if violations:
        return violations
    await repo.upsert(specialist_id, key, body)
    logger.info(
        "template.overridden",
        extra={"specialist_id": specialist_id, "template_key": key},
    )
    return []


async def reset_template(
    repo: MessageTemplatesRepo,
    *,
    specialist_id: int,
    key: str,
) -> bool:
    """Drop the override so the template resolves to its default again.

    Returns True when an override existed and was removed, False when there was
    nothing to reset (already the default).
    """
    removed = await repo.delete(specialist_id, key)
    if removed:
        logger.info(
            "template.reset",
            extra={"specialist_id": specialist_id, "template_key": key},
        )
    return removed
