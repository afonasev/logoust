from datetime import date, timedelta

from src.domain.schedule import next_weekday_on_or_after, series_occurrences

# 2026-06-01 is a Monday, so weekday indices map to known calendar dates below.
_MON = date(2026, 6, 1)
_WED = date(2026, 6, 3)


def test_next_weekday_returns_today_when_already_that_weekday():
    # Wednesday asked for Wednesday → the same day.
    assert next_weekday_on_or_after(_WED, 2) == _WED


def test_next_weekday_wraps_forward_within_the_week():
    # Wednesday asked for Monday → next Monday, not the past one.
    assert next_weekday_on_or_after(_WED, 0) == _MON + timedelta(days=7)


def test_next_weekday_same_week_future_day():
    # Monday asked for Wednesday → two days later, same week.
    assert next_weekday_on_or_after(_MON, 2) == _WED


def test_series_occurrences_steps_by_week():
    occ = series_occurrences(_MON, 0, _MON, date(2026, 6, 29))
    assert occ == [
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
        date(2026, 6, 22),
    ]


def test_series_occurrences_respects_range_start_after_start_date():
    occ = series_occurrences(_MON, 0, date(2026, 6, 10), date(2026, 6, 23))
    # First Monday at/after 2026-06-10 is the 15th; 22nd is in range, 29th is out.
    assert occ == [date(2026, 6, 15), date(2026, 6, 22)]


def test_series_occurrences_excludes_range_end_boundary():
    # Half-open interval: an occurrence exactly on range_end is excluded.
    occ = series_occurrences(_MON, 0, _MON, date(2026, 6, 8))
    assert occ == [date(2026, 6, 1)]


def test_series_occurrences_empty_range():
    assert series_occurrences(_MON, 0, _MON, _MON) == []


def test_series_occurrences_before_start_date():
    # Range entirely before start_date yields nothing.
    occ = series_occurrences(_MON, 0, date(2026, 5, 1), date(2026, 5, 31))
    assert occ == []
