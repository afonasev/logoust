from datetime import UTC, date, datetime

from src.domain.audit import AuditEvent
from src.domain.schedule import next_occurrence_utc, wall_to_utc
from src.domain.scheduled_message import (
    ScheduledClientMessage,
    ScheduledMessageStatus,
    appointment_target_key,
    is_message_due,
    schedule_target_key,
    slot_date_target_key,
)

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST


def _message(
    *, status: ScheduledMessageStatus, due_at: datetime
) -> ScheduledClientMessage:
    return ScheduledClientMessage(
        id=1,
        specialist_id=1,
        client_id=2,
        chat_id=555,
        text="привет",
        target_key="appt:1",
        event=AuditEvent.NOTIFY_CREATED,
        due_at=due_at,
        status=status,
        created_at=datetime(2026, 6, 6, 0, 0, tzinfo=UTC),
        sent_at=None,
    )


def test_next_occurrence_today_when_time_not_passed():
    # 10:00 UTC = 15:00 wall; 20:00 wall today has not passed yet → today.
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    due = next_occurrence_utc("20:00", now, _TZ)
    assert due == wall_to_utc(date(2026, 6, 6), "20:00", _TZ)


def test_next_occurrence_tomorrow_when_time_passed():
    # 18:00 UTC = 23:00 wall; 20:00 wall today already passed → tomorrow.
    now = datetime(2026, 6, 6, 18, 0, tzinfo=UTC)
    due = next_occurrence_utc("20:00", now, _TZ)
    assert due == wall_to_utc(date(2026, 6, 7), "20:00", _TZ)


def test_next_occurrence_boundary_equal_now_rolls_to_tomorrow():
    # When the wall time equals now exactly, `<= now` rolls it to tomorrow.
    now = wall_to_utc(date(2026, 6, 6), "20:00", _TZ)
    due = next_occurrence_utc("20:00", now, _TZ)
    assert due == wall_to_utc(date(2026, 6, 7), "20:00", _TZ)


def test_next_occurrence_other_timezone():
    # Moscow is UTC+3: 21:00 wall = 18:00 UTC; at 12:00 UTC it is still ahead → today.
    now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    due = next_occurrence_utc("21:00", now, "Europe/Moscow")
    assert due == datetime(2026, 6, 6, 18, 0, tzinfo=UTC)


def test_target_key_builders():
    assert appointment_target_key(7) == "appt:7"
    assert schedule_target_key(3) == "schedule:3"
    assert slot_date_target_key(3, date(2026, 6, 22)) == "slot:3:2026-06-22"


def test_is_message_due_true_when_queued_and_past():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    due = _message(
        status=ScheduledMessageStatus.QUEUED,
        due_at=datetime(2026, 6, 6, 11, 0, tzinfo=UTC),
    )
    assert is_message_due(due, now) is True


def test_is_message_due_false_when_future():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    future = _message(
        status=ScheduledMessageStatus.QUEUED,
        due_at=datetime(2026, 6, 6, 13, 0, tzinfo=UTC),
    )
    assert is_message_due(future, now) is False


def test_is_message_due_false_when_not_queued():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    sent = _message(
        status=ScheduledMessageStatus.SENT,
        due_at=datetime(2026, 6, 6, 11, 0, tzinfo=UTC),
    )
    assert is_message_due(sent, now) is False
