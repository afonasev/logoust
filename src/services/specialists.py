import enum
import logging

from src.domain.schedule import is_known_timezone, parse_hhmm
from src.domain.specialist import Specialist, SpecialistsRepo

logger = logging.getLogger(__name__)

_MAX_SLOT_MINUTES = 480  # 8 hours — guards against absurd input, not a hard rule.


class SettingField(enum.Enum):
    TIMEZONE = "timezone"
    DAY_START = "day_start"
    DAY_END = "day_end"
    SLOT_MINUTES = "slot_minutes"


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
    if field in {SettingField.DAY_START, SettingField.DAY_END}:
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
