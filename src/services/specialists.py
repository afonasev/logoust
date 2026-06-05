import enum
import logging

from src.domain.schedule import (
    format_working_days,
    is_known_timezone,
    parse_hhmm,
    parse_working_days,
)
from src.domain.specialist import Specialist, SpecialistsRepo

logger = logging.getLogger(__name__)

_MAX_SLOT_MINUTES = 480  # 8 hours — guards against absurd input, not a hard rule.


class SettingField(enum.Enum):
    TIMEZONE = "timezone"
    DAY_START = "day_start"
    DAY_END = "day_end"
    SLOT_MINUTES = "slot_minutes"
    REMINDER_TIME = "reminder_time"


class SettingsUpdateResult(enum.Enum):
    UPDATED = "updated"
    INVALID = "invalid"
    NOT_FOUND = "not_found"


async def get_settings(repo: SpecialistsRepo, specialist_id: int) -> Specialist | None:
    return await repo.get(specialist_id)


def _normalize(field: SettingField, raw: str) -> object | None:
    """Validate and canonicalise a setting value; None means invalid input."""
    value = raw.strip()
    if field is SettingField.TIMEZONE:
        return value if is_known_timezone(value) else None
    if field in {
        SettingField.DAY_START,
        SettingField.DAY_END,
        SettingField.REMINDER_TIME,
    }:
        return parse_hhmm(value)
    return _parse_slot_minutes(value)


def _parse_slot_minutes(value: str) -> int | None:
    if not value.isdigit():
        return None
    minutes = int(value)
    if minutes <= 0 or minutes > _MAX_SLOT_MINUTES:
        return None
    return minutes


async def update_setting(
    repo: SpecialistsRepo,
    *,
    specialist_id: int,
    field: SettingField,
    raw: str,
) -> SettingsUpdateResult:
    normalized = _normalize(field, raw)
    if normalized is None:
        return SettingsUpdateResult.INVALID
    updated = await repo.update_settings(specialist_id, {field.value: normalized})
    if updated is None:
        return SettingsUpdateResult.NOT_FOUND
    logger.info(
        "specialist.setting_updated",
        extra={"specialist_id": specialist_id, "field": field.value},
    )
    return SettingsUpdateResult.UPDATED


async def toggle_reminder(
    repo: SpecialistsRepo, *, specialist_id: int
) -> Specialist | None:
    """Flip the client-reminder on/off flag and persist it.

    Returns the updated specialist, or None if it does not exist.
    """
    specialist = await repo.get(specialist_id)
    if specialist is None:
        return None
    updated = await repo.update_settings(
        specialist_id, {"reminder_enabled": not specialist.reminder_enabled}
    )
    logger.info(
        "specialist.setting_updated",
        extra={"specialist_id": specialist_id, "field": "reminder_enabled"},
    )
    return updated


async def toggle_working_day(
    repo: SpecialistsRepo, *, specialist_id: int, weekday: int
) -> Specialist | None:
    """Flip one weekday in the specialist's working-days set and persist it.

    Reads the current set, inverts `weekday`, writes back the canonicalised
    string. Returns the updated specialist, or None if it does not exist.
    """
    specialist = await repo.get(specialist_id)
    if specialist is None:
        return None
    days = set(parse_working_days(specialist.working_days))
    if weekday in days:
        days.discard(weekday)
    else:
        days.add(weekday)
    updated = await repo.update_settings(
        specialist_id, {"working_days": format_working_days(sorted(days))}
    )
    logger.info(
        "specialist.setting_updated",
        extra={"specialist_id": specialist_id, "field": "working_days"},
    )
    return updated
