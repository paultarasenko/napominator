"""Recurrence rules and the pure ``next_occurrence`` function.

Occurrences are computed over the user's *wall-clock* time (``dtstart_local``) and only then
converted to UTC, so "every day at 9:00" stays 9:00 across DST changes (ADR-0006).

Engines
-------
* DAILY / WEEKLY  - ``dateutil.rrule`` (RFC 5545 semantics are exactly what we need).
* MONTHLY / YEARLY - a small explicit generator. RFC 5545 *skips* months without a 31st;
  the product rule is to *clamp* to the last day ("every 31st" -> Feb 28/29; Feb 29 yearly
  -> Feb 28 in common years).
* MINUTELY / HOURLY - absolute arithmetic in UTC (wall-clock hourly steps are meaningless
  across a DST change).
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Self
from zoneinfo import ZoneInfo

from dateutil import rrule as du

from napominator.domain.timeutil import local_to_utc

MIN_INTERVAL = timedelta(minutes=5)
MAX_HORIZON_YEARS = 10
_MAX_MONTHDAY = 31
_MAX_ITERATIONS = 100_000

_DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_RRULE_WEEKDAYS = (du.MO, du.TU, du.WE, du.TH, du.FR, du.SA, du.SU)


class Frequency(StrEnum):
    MINUTELY = "MINUTELY"
    HOURLY = "HOURLY"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"


_SUB_DAILY = {Frequency.MINUTELY: timedelta(minutes=1), Frequency.HOURLY: timedelta(hours=1)}


class InvalidRecurrenceError(ValueError):
    """The rule violates a domain constraint (interval, horizon, field combination)."""


@dataclass(frozen=True, slots=True)
class Recurrence:
    """A repeat rule. ``until_local`` is a naive local wall-clock instant (inclusive)."""

    freq: Frequency
    interval: int = 1
    byweekday: tuple[int, ...] = ()  # 0 = Monday
    bymonthday: tuple[int, ...] = ()  # 1..31 or -1 (last day)
    bymonth: tuple[int, ...] = ()
    until_local: datetime | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise InvalidRecurrenceError("interval must be >= 1")
        if self.freq in _SUB_DAILY and _SUB_DAILY[self.freq] * self.interval < MIN_INTERVAL:
            raise InvalidRecurrenceError("minimum repeat interval is 5 minutes")
        if self.count is not None and self.count < 1:
            raise InvalidRecurrenceError("count must be >= 1")
        if self.until_local is not None and self.until_local.tzinfo is not None:
            raise InvalidRecurrenceError("until_local must be naive local time")
        if self.byweekday and self.freq is not Frequency.WEEKLY:
            raise InvalidRecurrenceError("byweekday is only valid for WEEKLY")
        if self.bymonthday and self.freq is not Frequency.MONTHLY:
            raise InvalidRecurrenceError("bymonthday is only valid for MONTHLY")
        if self.bymonth and self.freq is not Frequency.YEARLY:
            raise InvalidRecurrenceError("bymonth is only valid for YEARLY")
        if any(not 0 <= d <= 6 for d in self.byweekday):  # noqa: PLR2004
            raise InvalidRecurrenceError("weekday must be 0..6")
        if any(d == 0 or not -1 <= d <= _MAX_MONTHDAY for d in self.bymonthday):
            raise InvalidRecurrenceError("month day must be 1..31 or -1")
        if any(not 1 <= m <= 12 for m in self.bymonth):  # noqa: PLR2004
            raise InvalidRecurrenceError("month must be 1..12")
        if len(self.bymonth) > 1:
            raise InvalidRecurrenceError("yearly rules support a single month")

    # --- RFC 5545-style (de)serialisation; UNTIL is stored as floating local time ---

    def to_rrule(self) -> str:
        parts = [f"FREQ={self.freq.value}"]
        if self.interval != 1:
            parts.append(f"INTERVAL={self.interval}")
        if self.byweekday:
            parts.append("BYDAY=" + ",".join(_DAYS[d] for d in sorted(set(self.byweekday))))
        if self.bymonthday:
            parts.append("BYMONTHDAY=" + ",".join(str(d) for d in sorted(set(self.bymonthday))))
        if self.bymonth:
            parts.append("BYMONTH=" + ",".join(str(m) for m in self.bymonth))
        if self.until_local is not None:
            parts.append("UNTIL=" + self.until_local.strftime("%Y%m%dT%H%M%S"))
        if self.count is not None:
            parts.append(f"COUNT={self.count}")
        return ";".join(parts)

    @classmethod
    def from_rrule(cls, text: str) -> Self:
        fields: dict[str, str] = {}
        for part in text.strip().removeprefix("RRULE:").split(";"):
            key, sep, value = part.partition("=")
            if not sep or not value:
                raise InvalidRecurrenceError(f"malformed RRULE part: {part!r}")
            fields[key.upper()] = value
        try:
            freq = Frequency(fields.pop("FREQ"))
            interval = int(fields.pop("INTERVAL", "1"))
            byday = fields.pop("BYDAY", "")
            bymonthday = fields.pop("BYMONTHDAY", "")
            bymonth = fields.pop("BYMONTH", "")
            until = fields.pop("UNTIL", "")
            count = fields.pop("COUNT", "")
            weekdays = tuple(_DAYS.index(d) for d in byday.split(",")) if byday else ()
            rule = cls(
                freq=freq,
                interval=interval,
                byweekday=weekdays,
                bymonthday=tuple(int(d) for d in bymonthday.split(",")) if bymonthday else (),
                bymonth=tuple(int(m) for m in bymonth.split(",")) if bymonth else (),
                until_local=datetime.strptime(until, "%Y%m%dT%H%M%S") if until else None,  # noqa: DTZ007
                count=int(count) if count else None,
            )
        except (KeyError, ValueError) as exc:
            raise InvalidRecurrenceError(f"invalid RRULE: {text!r}") from exc
        if fields:
            raise InvalidRecurrenceError(f"unsupported RRULE fields: {sorted(fields)}")
        return rule


@dataclass(frozen=True, slots=True)
class Schedule:
    """What the user asked for: an anchor in local wall-clock time and an optional rule."""

    dtstart_local: datetime
    tz: ZoneInfo
    recurrence: Recurrence | None = None

    def __post_init__(self) -> None:
        if self.dtstart_local.tzinfo is not None:
            raise InvalidRecurrenceError("dtstart_local must be naive local time")


def next_occurrence(schedule: Schedule, after_utc: datetime) -> datetime | None:
    """First occurrence strictly after ``after_utc`` (aware UTC), or ``None``.

    Pure and deterministic. Returns ``None`` for a finished one-off, an exhausted series
    (``COUNT``/``UNTIL``) and anything further than ``MAX_HORIZON_YEARS`` ahead.
    """
    if after_utc.tzinfo is None or after_utc.utcoffset() is None:
        raise ValueError("after_utc must be aware")
    after = after_utc.astimezone(UTC)
    horizon = after + timedelta(days=365 * MAX_HORIZON_YEARS + 3)
    for moment in _occurrences_utc(schedule):
        if moment > horizon:
            return None
        if moment > after:
            return moment
    return None


def occurrences(schedule: Schedule, after_utc: datetime, limit: int) -> list[datetime]:
    """Up to ``limit`` consecutive occurrences after ``after_utc`` (for previews and tests)."""
    result: list[datetime] = []
    cursor = after_utc
    while len(result) < limit:
        nxt = next_occurrence(schedule, cursor)
        if nxt is None:
            break
        result.append(nxt)
        cursor = nxt
    return result


# --- generators ---


def _occurrences_utc(schedule: Schedule) -> Iterator[datetime]:
    rule = schedule.recurrence
    if rule is None:
        yield local_to_utc(schedule.dtstart_local, schedule.tz)
        return
    if rule.freq in _SUB_DAILY:
        yield from _sub_daily(schedule, rule)
        return
    previous: datetime | None = None
    for local in _wall_clock(schedule.dtstart_local, rule):
        moment = local_to_utc(local, schedule.tz)
        if previous is not None and moment <= previous:
            continue  # two wall-clock times collapsed onto one instant: fire once
        previous = moment
        yield moment


def _sub_daily(schedule: Schedule, rule: Recurrence) -> Iterator[datetime]:
    step = _SUB_DAILY[rule.freq] * rule.interval
    start = local_to_utc(schedule.dtstart_local, schedule.tz)
    until = local_to_utc(rule.until_local, schedule.tz) if rule.until_local else None
    index = 0
    while rule.count is None or index < rule.count:
        moment = start + step * index
        if until is not None and moment > until:
            return
        yield moment
        index += 1


def _wall_clock(start: datetime, rule: Recurrence) -> Iterator[datetime]:
    """Naive local occurrences in ascending order, honouring ``COUNT`` and ``UNTIL``."""
    if rule.freq in (Frequency.DAILY, Frequency.WEEKLY):
        yield from _rrule_engine(start, rule)
        return
    for emitted, candidate in enumerate(_monthly_or_yearly(start, rule)):
        if rule.until_local is not None and candidate > rule.until_local:
            return
        if rule.count is not None and emitted >= rule.count:
            return
        yield candidate


def _rrule_engine(start: datetime, rule: Recurrence) -> Iterator[datetime]:
    freq: Literal[2, 3] = du.DAILY if rule.freq is Frequency.DAILY else du.WEEKLY
    weekdays: tuple[du.weekday, ...] | None = None
    if rule.freq is Frequency.WEEKLY:
        days = rule.byweekday or (start.weekday(),)
        weekdays = tuple(_RRULE_WEEKDAYS[d] for d in days)
    generator = du.rrule(
        freq,
        dtstart=start,
        interval=rule.interval,
        byweekday=weekdays,
        until=rule.until_local,
        count=rule.count,
    )
    # dateutil yields lazily; our consumers stop as soon as they have what they need.
    yield from generator


def _monthly_or_yearly(start: datetime, rule: Recurrence) -> Iterator[datetime]:
    clock = start.time()
    if rule.freq is Frequency.MONTHLY:
        days = tuple(rule.bymonthday) or (start.day,)
        for step in range(_MAX_ITERATIONS):
            year, month = _add_months(start.year, start.month, step * rule.interval)
            for day in sorted({_clamp_day(year, month, d) for d in days}):
                candidate = datetime.combine(datetime(year, month, day).date(), clock)  # noqa: DTZ001
                if candidate >= start:
                    yield candidate
    else:
        month = rule.bymonth[0] if rule.bymonth else start.month
        for step in range(_MAX_ITERATIONS):
            year = start.year + step * rule.interval
            if year > 9999:  # noqa: PLR2004
                return
            day = min(start.day, calendar.monthrange(year, month)[1])
            candidate = datetime.combine(datetime(year, month, day).date(), clock)  # noqa: DTZ001
            if candidate >= start:
                yield candidate


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _clamp_day(year: int, month: int, day: int) -> int:
    last = calendar.monthrange(year, month)[1]
    return last if day < 0 else min(day, last)


__all__ = [
    "MAX_HORIZON_YEARS",
    "MIN_INTERVAL",
    "Frequency",
    "InvalidRecurrenceError",
    "Recurrence",
    "Schedule",
    "next_occurrence",
    "occurrences",
]
