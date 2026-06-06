"""Use-case for the specialist's morning digest: today's appointment list.

The pass is gated by the pure `is_digest_due` and, when due, snapshots today's
appointments (real rows + virtual series repeats, exactly as the day screen shows
them), renders one text message, and sends it via the injected `send` callable —
aiogram stays out of services. The day is stamped *before* sending so a delivery
failure still leaves it marked done (no retry-loop); an empty day sends nothing
but is stamped all the same (design.md, decisions 2 and 5).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import logging

from src.domain.appointment import Appointment, AppointmentsRepo
from src.domain.client import ClientsRepo
from src.domain.recurring import RecurringExceptionsRepo, RecurringRepo
from src.domain.schedule import format_ru_date, today_in_tz, utc_to_wall
from src.domain.specialist import Specialist, SpecialistsRepo, is_digest_due
from src.services.appointments import list_specialist_day
from src.services.clients import client_name_map
from src.services.recurring import load_series_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DigestMessages:
    """Digest render texts pulled from messages.toml, threaded into the service."""

    title: str
    line: str
    comment_suffix: str
    dash: str


def _comment_part(comment: str | None, messages: DigestMessages) -> str:
    return messages.comment_suffix.format(comment=comment) if comment else ""


def render_digest(
    occurrences: list[Appointment],
    names: dict[int, str],
    day: datetime,
    tz: str,
    messages: DigestMessages,
) -> str:
    """Heading + one ``time — child (comment)`` line per occurrence, time-ascending."""
    header = messages.title.format(date=format_ru_date(day.date()))
    lines = [
        messages.line.format(
            time=f"{utc_to_wall(occ.starts_at, tz):%H:%M}",
            child=names.get(occ.client_id, messages.dash),
            comment=_comment_part(occ.comment, messages),
        )
        for occ in occurrences
    ]
    return "\n".join([header, *lines])


async def collect_today_digest(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    recurring_repo: RecurringRepo,
    exceptions_repo: RecurringExceptionsRepo,
    clients_repo: ClientsRepo,
    messages: DigestMessages,
) -> str | None:
    """Rendered digest for today in the specialist's tz, or None when no meetings.

    Shared by the scheduled pass and the manual "send now" action so both produce
    the identical list (real rows + virtual series repeats landing today).
    """
    assert specialist.id is not None  # noqa: S101 — caller passes a persisted specialist
    tz = specialist.timezone
    today = today_in_tz(now, tz)
    series = await load_series_context(
        recurring_repo, exceptions_repo, specialist_id=specialist.id, now=now, tz=tz
    )
    occurrences = await list_specialist_day(
        appointments_repo, specialist_id=specialist.id, day=today, tz=tz, series=series
    )
    if not occurrences:
        return None
    names = await client_name_map(clients_repo, specialist_id=specialist.id)
    wall_today = utc_to_wall(now, tz)
    return render_digest(occurrences, names, wall_today, tz, messages)


async def send_digest_if_due(  # noqa: PLR0913
    specialist: Specialist,
    now: datetime,
    *,
    appointments_repo: AppointmentsRepo,
    specialists_repo: SpecialistsRepo,
    recurring_repo: RecurringRepo,
    exceptions_repo: RecurringExceptionsRepo,
    clients_repo: ClientsRepo,
    messages: DigestMessages,
    send: Callable[[int, str], Awaitable[object]],
) -> bool:
    """Send today's digest to the specialist when the pass is due; True if sent.

    `send` may raise on a delivery failure — the day is already stamped by then, so
    the caller catches and logs without re-trying that day.
    """
    if not is_digest_due(specialist, now):
        return False
    assert specialist.id is not None  # noqa: S101 — candidates are persisted
    assert specialist.telegram_chat_id is not None  # noqa: S101 — candidates are welcomed
    text = await collect_today_digest(
        specialist,
        now,
        appointments_repo=appointments_repo,
        recurring_repo=recurring_repo,
        exceptions_repo=exceptions_repo,
        clients_repo=clients_repo,
        messages=messages,
    )
    # Stamp the day before delivery: a failed send must not re-trigger the pass.
    await specialists_repo.mark_digest_run(
        specialist.id, today_in_tz(now, specialist.timezone)
    )
    if text is None:
        logger.info(
            "specialist.digest_skipped_empty", extra={"specialist_id": specialist.id}
        )
        return False
    await send(specialist.telegram_chat_id, text)
    logger.info("specialist.digest_sent", extra={"specialist_id": specialist.id})
    return True
