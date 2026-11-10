"""Intermediate representation: what each matcher found, before conflicts are resolved."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from napominator.domain.recurrence import Frequency

# Higher wins when two fragments overlap.
P_CATEGORY = 9
P_RECURRENCE = 9
P_LIMIT = 8
P_LEAD = 8
P_DELTA = 7
P_CLOCK = 7
P_DATE = 6
P_NAG = 5
P_DAYPART = 4


@dataclass(frozen=True, slots=True)
class DayOffset:
    days: int


@dataclass(frozen=True, slots=True)
class WeekdayRef:
    weekday: int  # 0 = Monday
    modifier: str = ""  # "" | "this" | "next"


@dataclass(frozen=True, slots=True)
class CalendarDate:
    day: int
    month: int
    year: int | None
    at_midnight: bool = False  # a moment (Новый год): "за 6 часов до" counts from 00:00


@dataclass(frozen=True, slots=True)
class DayOfMonth:
    """``15 числа`` / ``15-го``: the next such day of a month, month not named."""

    day: int


@dataclass(frozen=True, slots=True)
class Delta:
    """Relative offset: ``minutes`` are absolute, ``days``/``months`` are calendar units."""

    minutes: int = 0
    days: int = 0
    months: int = 0

    def __add__(self, other: Delta) -> Delta:
        return Delta(
            self.minutes + other.minutes, self.days + other.days, self.months + other.months
        )

    @property
    def calendar_based(self) -> bool:
        return self.days != 0 or self.months != 0


@dataclass(frozen=True, slots=True)
class ClockRef:
    hour: int  # 0..24
    minute: int
    daypart: str | None  # morning | day | evening | night
    exact: bool  # 24-hour reading is unambiguous
    uncertain: bool = False  # the number may not be a time at all


@dataclass(frozen=True, slots=True)
class DayPartRef:
    name: str  # morning | midday | evening | afternoon | before_lunch | after_work | ...
    uncertain: bool = False  # "после обеда", "рано утром": the hour is only a guess


@dataclass(frozen=True, slots=True)
class RuleRef:
    freq: Frequency
    interval: int = 1
    byweekday: tuple[int, ...] = ()
    bymonthday: tuple[int, ...] = ()
    daypart: str | None = None
    too_frequent: bool = False


@dataclass(frozen=True, slots=True)
class UntilEnd:
    unit: str  # week | month | year


@dataclass(frozen=True, slots=True)
class ForSpan:
    """``в течение недели`` / ``на протяжении 5 дней``: how long a repeat lasts."""

    delta: Delta


@dataclass(frozen=True, slots=True)
class Unsupported:
    """A phrase we recognise but cannot schedule (``в последнюю пятницу месяца``): ask instead."""


@dataclass(frozen=True, slots=True)
class CountLimit:
    count: int


@dataclass(frozen=True, slots=True)
class CategoryRef:
    name: str


@dataclass(frozen=True, slots=True)
class LeadRef:
    delta: timedelta
    # "за 2 дня до 17 ноября": the offset is counted back from the event that follows "до",
    # so it moves the reminder itself instead of adding an advance notice.
    anchored: bool = False
    # "за час до встречи …": "до" is followed by plain words, the event is given elsewhere
    loose: bool = False


@dataclass(frozen=True, slots=True)
class NagRef:
    pass


@dataclass(frozen=True, slots=True)
class Fragment:
    start: int  # token index, inclusive
    end: int  # token index, exclusive
    priority: int
    data: object
