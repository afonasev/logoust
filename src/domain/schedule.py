"""Pure time/scheduling helpers shared across services and the bot.

No SQLAlchemy or aiogram imports — only stdlib. All wall-clock ↔ UTC conversion
goes through `zoneinfo` and an explicit specialist timezone; we never rely on the
deploy server's local time (see design.md, decision 1).
"""

from datetime import UTC, date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

# Curated list of Russian timezones offered in settings. IANA values keep
# `zoneinfo` correct forever; labels carry a Moscow-relative hint for the UI.
RUSSIAN_TIMEZONES: list[tuple[str, str]] = [
    ("Europe/Kaliningrad", "Калининград (МСК-1)"),  # noqa: RUF001
    ("Europe/Moscow", "Москва (МСК)"),  # noqa: RUF001
    ("Europe/Samara", "Самара (МСК+1)"),  # noqa: RUF001
    ("Asia/Yekaterinburg", "Екатеринбург (МСК+2)"),  # noqa: RUF001
    ("Asia/Omsk", "Омск (МСК+3)"),  # noqa: RUF001
    ("Asia/Krasnoyarsk", "Красноярск (МСК+4)"),  # noqa: RUF001
    ("Asia/Irkutsk", "Иркутск (МСК+5)"),  # noqa: RUF001
    ("Asia/Yakutsk", "Якутск (МСК+6)"),  # noqa: RUF001
    ("Asia/Vladivostok", "Владивосток (МСК+7)"),  # noqa: RUF001
    ("Asia/Magadan", "Магадан (МСК+8)"),  # noqa: RUF001
    ("Asia/Kamchatka", "Камчатка (МСК+9)"),  # noqa: RUF001
]

_TIMEZONE_VALUES = frozenset(value for value, _ in RUSSIAN_TIMEZONES)

# Genitive month names: Russian dates read "2 мая", not "2 май". Built by hand so
# we never depend on a system locale that may be absent in the deploy image.
RU_MONTHS_GENITIVE: dict[int, str] = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

RU_MONTHS_NOMINATIVE: dict[int, str] = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

RU_WEEKDAYS: list[str] = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]

# "Every <weekday>" in the grammatically correct accusative case (gender varies),
# for the recurring-series rule line ("Каждый вторник", "Каждую среду", …).
RU_WEEKDAYS_EVERY: list[str] = [
    "Каждый понедельник",
    "Каждый вторник",
    "Каждую среду",
    "Каждый четверг",
    "Каждую пятницу",
    "Каждую субботу",
    "Каждое воскресенье",
]

# Short weekday headers for the inline calendar, Monday-first.
RU_WEEKDAYS_SHORT: list[str] = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]  # noqa: RUF001

_HHMM_RE = re.compile(r"([0-9]{1,2}):([0-9]{2})")
_MINUTES_IN_HOUR = 60
_MAX_HOUR = 23


def is_known_timezone(tz: str) -> bool:
    return tz in _TIMEZONE_VALUES


def parse_hhmm(raw: str) -> str | None:
    """Validate free-text time as `HH:MM` in 00:00-23:59, returning it zero-padded.

    Returns None when the format or range is invalid; the caller re-asks.
    """
    match = _HHMM_RE.fullmatch(raw.strip())
    if match is None:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    if hours > _MAX_HOUR or minutes >= _MINUTES_IN_HOUR:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * _MINUTES_IN_HOUR + int(minutes)


def _from_minutes(total: int) -> str:
    return f"{total // _MINUTES_IN_HOUR:02d}:{total % _MINUTES_IN_HOUR:02d}"


def generate_slots(day_start: str, day_end: str, slot_minutes: int) -> list[str]:
    """Slots from `day_start`, stepping `slot_minutes`, while start < `day_end`.

    Example: 14:00 / 19:40 / 50 → 14:00, 14:50, 15:40, 16:30, 17:20, 18:10, 19:00.
    """
    start = _to_minutes(day_start)
    end = _to_minutes(day_end)
    slots: list[str] = []
    current = start
    while current < end:
        slots.append(_from_minutes(current))
        current += slot_minutes
    return slots


def occupied_grid_slots(
    grid: list[str], starts: set[str], slot_minutes: int
) -> set[str]:
    """Grid cells whose interval overlaps any appointment start.

    A cell `s` is occupied by a start `a` when the half-open intervals
    `[a, a+slot)` and `[s, s+slot)` intersect — for equal durations that is
    `|a - s| < slot_minutes` (touching edges do not count: a 14:00 start does not
    occupy 14:30 at a 30-min step). Returns the subset of `grid` cells overlapped by
    at least one start, so a start off the grid (e.g. 14:10) still marks both
    neighbouring cells.
    """
    start_minutes = [_to_minutes(start) for start in starts]
    return {
        cell
        for cell in grid
        if any(abs(start - _to_minutes(cell)) < slot_minutes for start in start_minutes)
    }


_MAX_WEEKDAY = 6  # Sunday, with Monday = 0 (date.weekday convention).


def parse_working_days(raw: str) -> list[int]:
    """Parse a `working_days` string into sorted unique weekday indices (Mon=0…Sun=6).

    Tolerates blanks and out-of-range tokens so a malformed DB value never crashes
    the screen — invalid entries are simply dropped.
    """
    days: set[int] = set()
    for raw_token in raw.split(","):
        token = raw_token.strip()
        if token.isdigit() and 0 <= int(token) <= _MAX_WEEKDAY:
            days.add(int(token))
    return sorted(days)


def format_working_days(days: list[int]) -> str:
    """Canonicalise weekday indices into the stored `working_days` string."""
    return ",".join(str(d) for d in sorted(set(days)))


def nearest_working_day(
    start: date, working_days: set[int], *, forward: bool
) -> date | None:
    """Nearest working day at or beyond `start` in one direction (inclusive).

    `forward` scans toward later dates, otherwise earlier. Returns None when
    `working_days` is empty. A non-empty set always hits within seven consecutive
    days (a full week covers every weekday), so the scan is bounded.
    """
    if not working_days:
        return None
    step = timedelta(days=1 if forward else -1)
    cursor = start
    for _ in range(len(RU_WEEKDAYS)):
        if cursor.weekday() in working_days:
            return cursor
        cursor += step
    return None  # pragma: no cover - unreachable for a non-empty working set


def next_working_days(today: date, working_days: set[int], count: int) -> list[date]:
    """The next `count` dates from `today` (inclusive) whose weekday is a working day.

    Empty `working_days` returns `[]` — without this guard the scan would never
    find a working day and loop forever (see design.md, decision 2).
    """
    if not working_days:
        return []
    result: list[date] = []
    cursor = today
    while len(result) < count:
        if cursor.weekday() in working_days:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def next_weekday_on_or_after(today: date, weekday: int) -> date:
    """Nearest date with `weekday` (Mon=0…Sun=6) at `today` or later.

    Returns `today` itself when it already falls on `weekday`.
    """
    days_ahead = (weekday - today.weekday()) % len(RU_WEEKDAYS)
    return today + timedelta(days=days_ahead)


def series_occurrences(
    start_date: date, weekday: int, range_start: date, range_end: date
) -> list[date]:
    """Weekly occurrence dates in the half-open `[range_start, range_end)`.

    The series repeats every 7 days from `start_date` (all on `weekday`). Only
    dates at or after both `start_date` and `range_start` are returned, stepping
    by a week until `range_end` is reached. An empty range yields `[]`.
    """
    lower = max(start_date, range_start)
    cursor = next_weekday_on_or_after(lower, weekday)
    result: list[date] = []
    while cursor < range_end:
        result.append(cursor)
        cursor += timedelta(days=7)
    return result


def wall_to_utc(day: date, hhmm: str, tz: str) -> datetime:
    """Treat (`day`, `hhmm`) as wall time in `tz` and return the aware UTC instant."""
    hours, minutes = hhmm.split(":")
    local = datetime(
        day.year, day.month, day.day, int(hours), int(minutes), tzinfo=ZoneInfo(tz)
    )
    return local.astimezone(UTC)


def utc_to_wall(moment: datetime, tz: str) -> datetime:
    """Convert an aware UTC instant to wall time in `tz`."""
    return moment.astimezone(ZoneInfo(tz))


def today_in_tz(now: datetime, tz: str) -> date:
    """Calendar day in `tz` for the given UTC instant."""
    return now.astimezone(ZoneInfo(tz)).date()


def next_occurrence_utc(hhmm: str, now: datetime, tz: str) -> datetime:
    """Nearest future UTC instant of wall time `hhmm` in `tz`.

    Today in `tz` if that time has not passed yet, otherwise tomorrow. Building the
    next day from the wall date (not by adding 24h to the UTC instant) keeps the
    result on the intended wall clock across DST shifts.
    """
    today = today_in_tz(now, tz)
    candidate = wall_to_utc(today, hhmm, tz)
    if candidate <= now:
        candidate = wall_to_utc(today + timedelta(days=1), hhmm, tz)
    return candidate


def day_start_utc(day: date, tz: str) -> datetime:
    """UTC instant of midnight at the start of `day` in `tz`.

    Used as the future/history boundary: an appointment is "future" while its
    `starts_at` is at or after the start of today in the specialist's timezone,
    so today's appointments stay in the future feed all day.
    """
    local = datetime.combine(day, time.min, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC)


def format_ru_date(day: date) -> str:
    """`2 мая, вторник` — day number, genitive month, weekday."""
    return f"{day.day} {RU_MONTHS_GENITIVE[day.month]}, {RU_WEEKDAYS[day.weekday()]}"


def format_ru_short(day: date) -> str:
    """`2 мая` — day number and genitive month, without the weekday."""
    return f"{day.day} {RU_MONTHS_GENITIVE[day.month]}"
