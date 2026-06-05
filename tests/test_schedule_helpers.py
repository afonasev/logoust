from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from src.domain.schedule import (
    day_start_utc as boundary,
    format_ru_date,
    generate_slots,
    is_known_timezone,
    parse_hhmm,
    today_in_tz,
    utc_to_wall,
    wall_to_utc,
)

_TZ = "Asia/Yekaterinburg"  # UTC+5, no DST


def test_is_known_timezone():
    assert is_known_timezone("Europe/Moscow")
    assert not is_known_timezone("America/New_York")


def test_parse_hhmm_valid_and_padding():
    assert parse_hhmm("9:05") == "09:05"
    assert parse_hhmm(" 23:59 ") == "23:59"


def test_parse_hhmm_rejects_bad_format():
    assert parse_hhmm("9-05") is None
    assert parse_hhmm("abc") is None
    assert parse_hhmm("") is None


def test_parse_hhmm_rejects_out_of_range():
    assert parse_hhmm("24:00") is None  # hour > 23
    assert parse_hhmm("12:99") is None  # minute >= 60


def test_generate_slots_example_from_spec():
    assert generate_slots("14:00", "19:40", 50) == [
        "14:00",
        "14:50",
        "15:40",
        "16:30",
        "17:20",
        "18:10",
        "19:00",
    ]


def test_generate_slots_hourly():
    assert generate_slots("09:00", "12:00", 60) == ["09:00", "10:00", "11:00"]


def test_generate_slots_empty_when_start_not_before_end():
    assert generate_slots("20:00", "09:00", 60) == []


def test_wall_to_utc_and_back_round_trip():
    day = date(2026, 6, 4)
    moment = wall_to_utc(day, "14:00", _TZ)
    assert moment == datetime(2026, 6, 4, 9, 0, tzinfo=UTC)  # 14:00 +05 → 09:00 UTC
    wall = utc_to_wall(moment, _TZ)
    assert wall.hour == 14
    assert wall.date() == day


def test_today_in_tz_rolls_over_with_offset():
    # 22:30 UTC is already next day 03:30 in UTC+5.
    now = datetime(2026, 6, 4, 22, 30, tzinfo=UTC)
    assert today_in_tz(now, _TZ) == date(2026, 6, 5)


def test_day_start_utc_is_local_midnight():
    moment = boundary(date(2026, 6, 5), _TZ)
    assert moment == datetime(2026, 6, 4, 19, 0, tzinfo=UTC)  # 00:00 +05 → 19:00 UTC
    assert moment.astimezone(ZoneInfo(_TZ)).hour == 0


def test_format_ru_date_genitive_month_and_weekday():
    assert format_ru_date(date(2026, 5, 2)) == "2 мая, суббота"
