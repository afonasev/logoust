from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from src.domain.schedule import (
    day_start_utc as boundary,
    format_ru_date,
    format_working_days,
    generate_slots,
    is_known_timezone,
    nearest_working_day,
    next_working_days,
    occupied_grid_slots,
    parse_hhmm,
    parse_working_days,
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


_GRID_30 = ["13:30", "14:00", "14:30", "15:00"]


def test_occupied_grid_slots_off_grid_marks_two_neighbours():
    # 14:10 at a 30-min step overlaps [14:00,14:30) and [14:30,15:00).
    assert occupied_grid_slots(_GRID_30, {"14:10"}, 30) == {"14:00", "14:30"}


def test_occupied_grid_slots_on_grid_marks_single_cell():
    # An exact 14:00 start touches but does not cross 14:30 — only 14:00 is taken.
    assert occupied_grid_slots(_GRID_30, {"14:00"}, 30) == {"14:00"}


def test_occupied_grid_slots_boundaries_free():
    # 13:30 and 15:00 do not overlap [14:10, 14:40).
    occupied = occupied_grid_slots(_GRID_30, {"14:10"}, 30)
    assert "13:30" not in occupied
    assert "15:00" not in occupied


def test_occupied_grid_slots_empty_starts():
    assert occupied_grid_slots(_GRID_30, set(), 30) == set()


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


def test_parse_working_days_sorts_dedups_and_drops_invalid():
    assert parse_working_days("4,0,1,1, 2 ") == [0, 1, 2, 4]
    assert parse_working_days("7,-1,abc,3") == [3]  # out-of-range / non-digit dropped
    assert parse_working_days("") == []


def test_format_working_days_canonicalises():
    assert format_working_days([4, 0, 2, 0]) == "0,2,4"
    assert not format_working_days([])


def test_parse_format_round_trip():
    assert format_working_days(parse_working_days("0,1,2,3,4")) == "0,1,2,3,4"


def test_next_working_days_skips_weekend():
    # 2026-06-05 is Friday; Mon-Fri working days.
    days = next_working_days(date(2026, 6, 5), {0, 1, 2, 3, 4}, 5)
    assert days == [
        date(2026, 6, 5),  # Fri
        date(2026, 6, 8),  # Mon (Sat/Sun skipped)
        date(2026, 6, 9),
        date(2026, 6, 10),
        date(2026, 6, 11),
    ]


def test_next_working_days_includes_today_when_working():
    days = next_working_days(date(2026, 6, 4), {3}, 2)  # only Thursdays
    assert days == [date(2026, 6, 4), date(2026, 6, 11)]


def test_next_working_days_empty_set_returns_empty():
    assert next_working_days(date(2026, 6, 4), set(), 5) == []


def test_nearest_working_day_forward_skips_weekend():
    # 2026-06-06 is Saturday; nearest working day forward (Mon-Fri) is Monday 06-08.
    assert nearest_working_day(date(2026, 6, 6), {0, 1, 2, 3, 4}, forward=True) == date(
        2026, 6, 8
    )


def test_nearest_working_day_backward_skips_weekend():
    # From Saturday backward, nearest working day is Friday 06-05.
    assert nearest_working_day(
        date(2026, 6, 6), {0, 1, 2, 3, 4}, forward=False
    ) == date(2026, 6, 5)


def test_nearest_working_day_inclusive_when_start_is_working():
    assert nearest_working_day(date(2026, 6, 5), {4}, forward=True) == date(2026, 6, 5)


def test_nearest_working_day_empty_set_is_none():
    assert nearest_working_day(date(2026, 6, 5), set(), forward=True) is None
