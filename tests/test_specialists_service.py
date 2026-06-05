from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.specialist import Specialist
from src.infrastructure.specialists_repo import SqlAlchemySpecialistsRepo
from src.services.specialists import (
    SettingField,
    SettingsUpdateResult,
    get_settings,
    toggle_working_day,
    update_setting,
)


async def _seed(session: AsyncSession) -> int:
    repo = SqlAlchemySpecialistsRepo(session)
    saved = await repo.add(
        Specialist(
            id=None,
            invite_token="tok",
            telegram_chat_id=None,
            telegram_username=None,
            welcomed_at=None,
            created_at=datetime.now(UTC),
        )
    )
    assert saved.id is not None
    return saved.id


async def test_get_settings_returns_defaults(session: AsyncSession):
    specialist_id = await _seed(session)
    settings = await get_settings(SqlAlchemySpecialistsRepo(session), specialist_id)
    assert settings is not None
    assert settings.timezone == "Asia/Yekaterinburg"


async def test_get_settings_missing(session: AsyncSession):
    assert await get_settings(SqlAlchemySpecialistsRepo(session), 404) is None


async def test_update_timezone_valid(session: AsyncSession):
    specialist_id = await _seed(session)
    repo = SqlAlchemySpecialistsRepo(session)
    result = await update_setting(
        repo,
        specialist_id=specialist_id,
        field=SettingField.TIMEZONE,
        raw="Europe/Moscow",
    )
    assert result is SettingsUpdateResult.UPDATED
    updated = await get_settings(repo, specialist_id)
    assert updated is not None
    assert updated.timezone == "Europe/Moscow"


async def test_update_timezone_unknown_is_invalid(session: AsyncSession):
    specialist_id = await _seed(session)
    result = await update_setting(
        SqlAlchemySpecialistsRepo(session),
        specialist_id=specialist_id,
        field=SettingField.TIMEZONE,
        raw="Mars/Phobos",
    )
    assert result is SettingsUpdateResult.INVALID


async def test_update_day_start_valid_and_invalid(session: AsyncSession):
    specialist_id = await _seed(session)
    repo = SqlAlchemySpecialistsRepo(session)
    ok = await update_setting(
        repo, specialist_id=specialist_id, field=SettingField.DAY_START, raw="8:30"
    )
    assert ok is SettingsUpdateResult.UPDATED
    updated = await get_settings(repo, specialist_id)
    assert updated is not None
    assert updated.day_start == "08:30"

    bad = await update_setting(
        repo, specialist_id=specialist_id, field=SettingField.DAY_END, raw="25:00"
    )
    assert bad is SettingsUpdateResult.INVALID


async def test_update_slot_minutes_validation(session: AsyncSession):
    specialist_id = await _seed(session)
    repo = SqlAlchemySpecialistsRepo(session)
    assert (
        await update_setting(
            repo, specialist_id=specialist_id, field=SettingField.SLOT_MINUTES, raw="45"
        )
        is SettingsUpdateResult.UPDATED
    )
    assert (
        await update_setting(
            repo,
            specialist_id=specialist_id,
            field=SettingField.SLOT_MINUTES,
            raw="abc",
        )
        is SettingsUpdateResult.INVALID
    )
    assert (
        await update_setting(
            repo, specialist_id=specialist_id, field=SettingField.SLOT_MINUTES, raw="0"
        )
        is SettingsUpdateResult.INVALID
    )
    assert (
        await update_setting(
            repo,
            specialist_id=specialist_id,
            field=SettingField.SLOT_MINUTES,
            raw="999",
        )
        is SettingsUpdateResult.INVALID
    )


async def test_update_setting_not_found(session: AsyncSession):
    result = await update_setting(
        SqlAlchemySpecialistsRepo(session),
        specialist_id=404,
        field=SettingField.SLOT_MINUTES,
        raw="45",
    )
    assert result is SettingsUpdateResult.NOT_FOUND


async def test_toggle_working_day_removes_then_adds(session: AsyncSession):
    specialist_id = await _seed(session)
    repo = SqlAlchemySpecialistsRepo(session)
    # Default Mon-Fri → toggling Monday (0) drops it.
    updated = await toggle_working_day(repo, specialist_id=specialist_id, weekday=0)
    assert updated is not None
    assert updated.working_days == "1,2,3,4"
    # Toggling Saturday (5) adds it, keeping the set canonically sorted.
    updated = await toggle_working_day(repo, specialist_id=specialist_id, weekday=5)
    assert updated is not None
    assert updated.working_days == "1,2,3,4,5"


async def test_toggle_working_day_not_found(session: AsyncSession):
    repo = SqlAlchemySpecialistsRepo(session)
    assert await toggle_working_day(repo, specialist_id=404, weekday=0) is None
