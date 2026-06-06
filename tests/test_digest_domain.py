from datetime import UTC, date, datetime

from src.domain.specialist import Specialist, is_digest_due

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
        "morning_notify_time": "10:00",
    }
    base.update(overrides)
    return Specialist(**base)  # type: ignore[arg-type]


def test_due_at_notify_time():
    # 05:00 UTC → 10:00 wall in Yekaterinburg, exactly morning_notify_time.
    now = datetime(2026, 6, 15, 5, 0, tzinfo=UTC)
    assert is_digest_due(_specialist(), now) is True


def test_not_due_before_notify_time():
    # 04:00 UTC → 09:00 wall, before 10:00.
    now = datetime(2026, 6, 15, 4, 0, tzinfo=UTC)
    assert is_digest_due(_specialist(), now) is False


def test_not_due_when_already_run_today():
    now = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)  # 11:00 wall
    today = date(2026, 6, 15)
    assert is_digest_due(_specialist(morning_notify_last_run_on=today), now) is False


def test_not_due_when_disabled():
    now = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
    assert is_digest_due(_specialist(morning_notify_enabled=False), now) is False


def test_catch_up_after_downtime_same_day():
    # Bot was down at 10:00, wakes at 11:00 wall (06:00 UTC); last run was yesterday.
    now = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)
    specialist = _specialist(morning_notify_last_run_on=date(2026, 6, 14))
    assert is_digest_due(specialist, now) is True
