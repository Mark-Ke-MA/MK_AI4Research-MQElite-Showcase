"""Calendar-based memory-day and archive eligibility semantics."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mqelite.errors import ValidationError

DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_BOUNDARY = time(3, 0)


def load_timezone(name: str = DEFAULT_TIMEZONE) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError("configured timezone is unavailable") from exc


@dataclass(frozen=True, slots=True)
class MemoryDayWindow:
    memory_date: date
    start: datetime
    end: datetime

    @classmethod
    def for_date(
        cls,
        memory_date: date,
        *,
        timezone: str = DEFAULT_TIMEZONE,
        boundary: time = DEFAULT_BOUNDARY,
    ) -> "MemoryDayWindow":
        if boundary.tzinfo is not None:
            raise ValidationError("day boundary must be a naive local wall time")
        zone = load_timezone(timezone)
        start = datetime.combine(memory_date, boundary, tzinfo=zone)
        end = datetime.combine(memory_date + timedelta(days=1), boundary, tzinfo=zone)
        return cls(memory_date=memory_date, start=start, end=end)

    @property
    def elapsed_seconds(self) -> float:
        return self.end.timestamp() - self.start.timestamp()

    def contains(self, instant: datetime) -> bool:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValidationError("instant must include a UTC offset")
        value = instant.timestamp()
        return self.start.timestamp() <= value < self.end.timestamp()


def memory_date_for(
    instant: datetime,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    boundary: time = DEFAULT_BOUNDARY,
) -> date:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValidationError("instant must include a UTC offset")
    local = instant.astimezone(load_timezone(timezone))
    if local.timetz().replace(tzinfo=None) < boundary:
        return local.date() - timedelta(days=1)
    return local.date()


def hot_cutoff(current_memory_date: date, hot_window_days: int = 30) -> date:
    if not isinstance(hot_window_days, int) or isinstance(hot_window_days, bool) or hot_window_days < 0:
        raise ValidationError("hot_window_days must be a non-negative integer")
    return current_memory_date - timedelta(days=hot_window_days)


def month_last_date(year: int, month: int) -> date:
    try:
        return date(year, month, calendar.monthrange(year, month)[1])
    except (TypeError, ValueError) as exc:
        raise ValidationError("invalid calendar month") from exc


def archive_month_eligible(year: int, month: int, cutoff: date) -> bool:
    """Return the date-only eligibility predicate; completeness is checked later."""

    return month_last_date(year, month) < cutoff
