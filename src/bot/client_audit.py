"""Single funnel for journalling outgoing client messages.

Every code path that sends a message to a *client* (notifications, reminders,
link confirmations, future ones) MUST record a `message` audit row through this
helper with the actual delivery outcome — see `.claude/rules/bot.md`. Keeping one
funnel is what makes "all client messages are audited" enforceable rather than a
promise each new feature has to remember.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.audit import AuditEvent, DeliveryStatus
from src.infrastructure.audit_repo import SqlAlchemyAuditRepo
from src.services.audit import record_message


async def record_client_message(  # noqa: PLR0913
    session_factory: async_sessionmaker[AsyncSession],
    *,
    specialist_id: int,
    client_id: int,
    event: AuditEvent,
    text: str,
    status: DeliveryStatus,
    error: str | None = None,
) -> None:
    """Write a `message` audit row for one delivery attempt to a client.

    Call after the send with `status=SENT` on success or `status=FAILED` (+`error`)
    on a delivery failure — a failed delivery is still journalled, the "not
    delivered" fact is the valuable part.
    """
    async with session_factory() as session:
        await record_message(
            SqlAlchemyAuditRepo(session),
            specialist_id=specialist_id,
            client_id=client_id,
            event=event,
            text=text,
            status=status,
            error=error,
        )
