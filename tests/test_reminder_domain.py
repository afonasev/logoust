from datetime import UTC, date, datetime

from src.domain.reminder import is_reminder_due
from src.domain.specialist import Specialist

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST


def _specialist(**overrides: object) -> Specialist:
    base: dict[str, object] = {
        "id": 1,
        "invite_token": "t",
        "telegram_chat_id": None,
        "telegram_username": None,
        "welcomed_at": None,
        "created_at": datetime.now(UTC),
        "timezone": _TZ,
        "reminder_time": "12:00",
    }
    base.update(overrides)
    return Specialist(**base)  # type: ignore[arg-type]


def test_due_at_reminder_time():
    # 07:00 UTC → 12:00 wall in Yekaterinburg, exactly reminder_time.
    now = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
    assert is_reminder_due(_specialist(), now) is True


def test_not_due_before_reminder_time():
    # 06:00 UTC → 11:00 wall, before 12:00.
    now = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
    assert is_reminder_due(_specialist(), now) is False


def test_not_due_when_already_run_today():
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)  # 13:00 wall
    today = date(2026, 6, 15)
    assert is_reminder_due(_specialist(reminder_last_run_on=today), now) is False


def test_not_due_when_disabled():
    now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    assert is_reminder_due(_specialist(reminder_enabled=False), now) is False


def test_catch_up_after_downtime_same_day():
    # Bot was down at noon, wakes at 15:00 wall (10:00 UTC); last run was yesterday.
    now = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    specialist = _specialist(reminder_last_run_on=date(2026, 6, 14))
    assert is_reminder_due(specialist, now) is True
