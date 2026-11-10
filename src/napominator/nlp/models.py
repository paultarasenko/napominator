"""Public data model of the reminder parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from napominator.domain.recurrence import Recurrence, Schedule

# Create immediately at or above this confidence; below it the bot asks a clarifying question.
CONFIDENCE_THRESHOLD = 0.7
MAX_INPUT_LENGTH = 1000


class Flag(StrEnum):
    TIME_INFERRED = "time_inferred"  # no time given: a day-part anchor was used
    DATE_INFERRED = "date_inferred"  # no date given: today/tomorrow was chosen
    ROLLED_TO_FUTURE = "rolled_to_future"  # the time had passed today: moved to tomorrow
    AMBIGUOUS_HOUR = "ambiguous_hour"  # "в 6" could be 06:00 or 18:00
    PAST_DATE = "past_date"  # explicit date/time in the past
    INVALID_DATE = "invalid_date"  # e.g. 31 April
    NEEDS_TIME = "needs_time"  # the date is known but the default time has already passed
    TIME_UNCERTAIN = "time_uncertain"  # "в 5 корпусе": maybe not a time at all
    MULTIPLE_TIMES = "multiple_times"  # several conflicting times in one message
    RECURRENCE_TOO_FREQUENT = "recurrence_too_frequent"  # below the 5-minute floor
    PAST_UNTIL = "past_until"  # the repeat limit ends before the first occurrence
    EMPTY_TEXT = "empty_text"  # nothing is left once the schedule words are removed
    TOO_LONG = "too_long"  # input was truncated to MAX_INPUT_LENGTH


@dataclass(frozen=True, slots=True)
class Anchors:
    """Per-user times of day for "утром", "днём", "вечером" and for date-only input."""

    morning: time = time(9, 0)
    midday: time = time(13, 0)
    evening: time = time(19, 0)


@dataclass(frozen=True, slots=True)
class ParseContext:
    now: datetime  # aware, UTC
    tz: ZoneInfo
    anchors: Anchors = field(default_factory=Anchors)
    categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            msg = "ParseContext.now must be an aware datetime"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    kind: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    text: str
    when: datetime | None  # naive *local* wall-clock time of the first occurrence
    recurrence: Recurrence | None = None
    category_ref: str | None = None  # explicit "#work" / "категория работа"
    category: str | None = None  # best fuzzy match among ``ctx.categories``
    lead: timedelta | None = None
    nag: bool = False
    confidence: float = 0.0
    flags: frozenset[Flag] = frozenset()
    spans: tuple[Span, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        """True when the bot must ask instead of creating the reminder right away."""
        return (
            self.when is None
            or not self.text
            or self.confidence < CONFIDENCE_THRESHOLD
            or Flag.PAST_DATE in self.flags
        )

    def schedule(self, tz: ZoneInfo) -> Schedule | None:
        if self.when is None:
            return None
        return Schedule(self.when, tz, self.recurrence)
