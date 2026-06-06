from datetime import UTC, date, datetime

from src.domain.specialist import Specialist, is_consumption_due

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST


def _specialist(**overrides: object) -> Specialist:
    base: dict[str, object] = {
        "id": 1,
        "invite_token": "t",
        "telegram_chat_id": 100,
        "telegram_username": None,
        "welcomed_at": None,
        "created_at": datetime.now(UTC),
        "timezone": _TZ,
        "consumption_time": "20:00",
    }
    base.update(overrides)
    return Specialist(**base)  # type: ignore[arg-type]


def test_due_at_consumption_time():
    # 15:00 UTC → 20:00 wall in Yekaterinburg, exactly consumption_time.
    now = datetime(2026, 6, 15, 15, 0, tzinfo=UTC)
    assert is_consumption_due(_specialist(), now) is True


def test_not_due_before_consumption_time():
    # 14:00 UTC → 19:00 wall, before 20:00.
    now = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
    assert is_consumption_due(_specialist(), now) is False


def test_not_due_when_already_run_today():
    now = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)  # 21:00 wall
    today = date(2026, 6, 15)
    assert is_consumption_due(_specialist(consumption_last_run_on=today), now) is False


def test_not_due_when_disabled():
    now = datetime(2026, 6, 15, 16, 0, tzinfo=UTC)
    assert is_consumption_due(_specialist(consumption_enabled=False), now) is False


def test_catch_up_after_downtime_same_day():
    # Bot was down at 20:00, wakes at 22:00 wall (17:00 UTC); last run was yesterday.
    now = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)
    specialist = _specialist(consumption_last_run_on=date(2026, 6, 14))
    assert is_consumption_due(specialist, now) is True
