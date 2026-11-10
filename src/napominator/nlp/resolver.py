"""Turns selected fragments into a concrete first occurrence (and a recurrence rule)."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from napominator.domain.recurrence import Frequency, InvalidRecurrenceError, Recurrence
from napominator.domain.timeutil import utc_to_local
from napominator.nlp import fragments as fr
from napominator.nlp.fragments import Fragment
from napominator.nlp.models import Flag, ParseContext
from napominator.nlp.tokenizer import Token

_WINDOW_FROM = 7  # ambiguous hours are only read inside 07:00..23:59
_SCAN_DAYS = 800
_MAX_COUNT = 1000
_END_OF_DAY = time(23, 59, 59)

FLAG_CAPS: dict[Flag, float] = {
    Flag.TIME_INFERRED: 0.9,
    Flag.DATE_INFERRED: 0.9,
    Flag.ROLLED_TO_FUTURE: 0.9,
    Flag.AMBIGUOUS_HOUR: 0.85,
    Flag.TIME_UNCERTAIN: 0.5,
    Flag.MULTIPLE_TIMES: 0.5,
    Flag.PAST_UNTIL: 0.4,
    Flag.RECURRENCE_TOO_FREQUENT: 0.4,
    Flag.EMPTY_TEXT: 0.5,
}


@dataclass(slots=True)
class Resolution:
    when: datetime | None = None
    recurrence: Recurrence | None = None
    lead: timedelta | None = None
    nag: bool = False
    category_ref: str | None = None
    flags: set[Flag] = field(default_factory=set)
    used: list[Fragment] = field(default_factory=list)  # fragments to cut from the text
    absorbed_until: list[int] = field(default_factory=list)  # token indices of "до"
    noun_lead: bool = False  # "за час до встречи …" was turned into a shift; the noun is left


def confidence_for(when: datetime | None, flags: set[Flag]) -> float:
    if when is None:
        return 0.0
    return min([1.0, *(FLAG_CAPS[f] for f in flags if f in FLAG_CAPS)])


def select(fragments: list[Fragment]) -> list[Fragment]:
    """Greedy non-overlapping choice: priority first, then length, then position."""
    taken: set[int] = set()
    chosen: list[Fragment] = []
    for frag in sorted(fragments, key=lambda f: (-f.priority, -(f.end - f.start), f.start)):
        span = range(frag.start, frag.end)
        if taken.isdisjoint(span):
            taken.update(span)
            chosen.append(frag)
    return sorted(chosen, key=lambda f: f.start)


class _Resolver:
    def __init__(self, chosen: list[Fragment], tokens: list[Token], ctx: ParseContext) -> None:
        self.chosen = chosen
        self.tokens = tokens
        self.ctx = ctx
        self.out = Resolution()
        self.now_local = utc_to_local(ctx.now, ctx.tz)
        self.today = self.now_local.date()
        self.threshold = self.now_local.replace(second=0, microsecond=0) + timedelta(minutes=1)
        self.span: fr.Delta | None = None  # "в течение недели": the repeat lasts this long
        self.shift = timedelta(0)  # "за N до <событие>": the reminder fires this long before it

    def of(self, kind: type) -> list[Fragment]:
        return [f for f in self.chosen if isinstance(f.data, kind)]

    def use(self, frag: Fragment) -> None:
        self.out.used.append(frag)

    # --- orchestration ---

    def run(self) -> Resolution:
        out = self.out
        for frag in self.of(fr.NagRef):
            out.nag = True
            self.use(frag)
        for frag in self.of(fr.CategoryRef)[:1]:
            assert isinstance(frag.data, fr.CategoryRef)  # noqa: S101
            out.category_ref = frag.data.name
            self.use(frag)
        for frag in self.of(fr.Unsupported)[:1]:  # recognised but not schedulable: ask "когда"
            self.use(frag)
            return out
        rules = self.of(fr.RuleRef)
        deltas = self.of(fr.Delta)
        date_frags = self._date_fragments()
        clocks = self._certain_clocks()
        dayparts = self.of(fr.DayPartRef)

        for frag in self.of(fr.LeadRef)[:1]:
            assert isinstance(frag.data, fr.LeadRef)  # noqa: S101
            self.use(frag)
            if (rules or deltas) or not self._is_shift(frag.data, bool(date_frags), bool(clocks)):
                out.lead = frag.data.delta
            else:
                self.shift = frag.data.delta
                out.noun_lead = frag.data.loose

        # "до 1 декабря" / "по 1 декабря" end a repeat: they are not a second date
        plain_dates = [f for f in date_frags if not (rules and self._is_until_date(f))]
        if len(clocks) > 1 or len(plain_dates) > 1 or len(dayparts) > 1:
            out.flags.add(Flag.MULTIPLE_TIMES)

        if rules:
            self._recurring(rules[0], date_frags, clocks, dayparts)
        elif deltas:
            self._relative(deltas[0], clocks)
        else:
            self._one_off(date_frags, clocks, dayparts)
        return out

    def _certain_clocks(self) -> list[Fragment]:
        """Clock fragments; doubtful ones ("в 3 корпусе") only count when nothing else does."""
        clocks = self.of(fr.ClockRef)
        certain = [f for f in clocks if not (isinstance(f.data, fr.ClockRef) and f.data.uncertain)]
        return certain or clocks

    @staticmethod
    def _is_shift(lead: fr.LeadRef, has_date: bool, has_clock: bool) -> bool:
        """Does "за N до …" move the reminder itself, rather than add an advance notice?"""
        if lead.anchored:
            return True
        # "за час до встречи завтра в 15:00": the event is named elsewhere in the phrase; an
        # hour-level offset needs a clock to count from, a day-level one needs just a date
        whole_days = lead.delta % timedelta(days=1) == timedelta(0)
        return lead.loose and (has_clock or (has_date and whole_days))

    def _is_until_date(self, frag: Fragment) -> bool:
        return isinstance(frag.data, fr.CalendarDate) and self.tokens[frag.start].text in {
            "до",
            "по",
        }

    def _date_fragments(self) -> list[Fragment]:
        """Date-like fragments in text order, minus those consumed as ``до <date>`` limits."""
        kinds = (fr.DayOffset, fr.WeekdayRef, fr.CalendarDate, fr.DayOfMonth)
        return [f for f in self.chosen if isinstance(f.data, kinds)]

    # --- relative ("через ...") ---

    def _relative(self, frag: Fragment, clocks: list[Fragment]) -> None:
        delta = frag.data
        assert isinstance(delta, fr.Delta)  # noqa: S101
        self.use(frag)
        if not delta.calendar_based:
            target = ctx_now_floor(self.ctx.now) + timedelta(minutes=delta.minutes)
            self.out.when = utc_to_local(target, self.ctx.tz)
            return
        day = self.today + timedelta(days=delta.days) + relativedelta(months=delta.months)
        if delta.minutes:
            self.out.when = (
                self.now_local.replace(second=0, microsecond=0)
                + (day - self.today)
                + timedelta(minutes=delta.minutes)
            )
            return
        if clocks:
            times, ambiguous = self._times(clocks[0], recurring=False)
            self.use(clocks[0])
            if ambiguous:
                self.out.flags.add(Flag.AMBIGUOUS_HOUR)
            self.out.when = datetime.combine(day, min(times))
            return
        self.out.flags.add(Flag.TIME_INFERRED)
        self.out.when = datetime.combine(day, self.ctx.anchors.morning)

    # --- one-off ---

    def _one_off(
        self, dates: list[Fragment], clocks: list[Fragment], dayparts: list[Fragment]
    ) -> None:
        out = self.out
        date_frag = dates[0] if dates else None
        if self._is_midnight_moment(date_frag, clocks, dayparts):
            times, default_time = [time(0, 0)], False
        else:
            times, default_time = self._time_options(
                clocks, dayparts, need_default=date_frag is not None
            )
        if date_frag is None and not times:
            return
        if date_frag is None:
            out.flags.add(Flag.DATE_INFERRED)
            candidates = [self._today_or_tomorrow(t) for t in times]
            out.when = min(candidates)
            if out.when.date() != self.today:
                out.flags.add(Flag.ROLLED_TO_FUTURE)
            return
        self.use(date_frag)
        candidates_dt = []
        for t in times:
            dt = self._date_at(date_frag.data, t)
            if dt is None:
                out.flags.add(Flag.INVALID_DATE)
                return
            candidates_dt.append(dt - self.shift)
        future = [dt for dt in candidates_dt if dt >= self.threshold]
        if future:
            out.when = min(future)
            return
        earliest = min(candidates_dt)
        if earliest.date() == self.today and default_time:
            out.flags.add(Flag.NEEDS_TIME)  # e.g. "сегодня позвонить" at 15:00
        else:
            out.flags.add(Flag.PAST_DATE)

    def _is_midnight_moment(
        self, date_frag: Fragment | None, clocks: list[Fragment], dayparts: list[Fragment]
    ) -> bool:
        """Counting back from midnight of 1 January ("за 6 часов до нового года"), not 09:00."""
        return (
            self.shift % timedelta(days=1) != timedelta(0)  # whole days count from the morning
            and date_frag is not None
            and not clocks
            and not dayparts
            and isinstance(date_frag.data, fr.CalendarDate)
            and date_frag.data.at_midnight
        )

    def _today_or_tomorrow(self, t: time) -> datetime:
        today_dt = datetime.combine(self.today, t) - self.shift
        if today_dt >= self.threshold:
            return today_dt
        return datetime.combine(self.today + timedelta(days=1), t) - self.shift

    def _time_options(
        self,
        clocks: list[Fragment],
        dayparts: list[Fragment],
        *,
        need_default: bool,
        recurring: bool = False,
    ) -> tuple[list[time], bool]:
        """Candidate times of day and whether they are defaults (not typed by the user)."""
        if clocks:
            frag = clocks[0]
            self.use(frag)
            times, ambiguous = self._times(frag, recurring=recurring)
            assert isinstance(frag.data, fr.ClockRef)  # noqa: S101
            if ambiguous:
                self.out.flags.add(Flag.AMBIGUOUS_HOUR)
            if frag.data.uncertain:
                self.out.flags.add(Flag.TIME_UNCERTAIN)
            return times, False
        if dayparts:
            frag = dayparts[0]
            self.use(frag)
            assert isinstance(frag.data, fr.DayPartRef)  # noqa: S101
            self.out.flags.add(Flag.TIME_INFERRED)
            if frag.data.uncertain:  # "после обеда": propose a time, the user confirms it
                self.out.flags.add(Flag.TIME_UNCERTAIN)
            return [self._anchor(frag.data.name)], True
        if need_default:
            self.out.flags.add(Flag.TIME_INFERRED)
            return [self.ctx.anchors.morning], True
        return [], True

    def _anchor(self, name: str) -> time:
        anchors = self.ctx.anchors
        if name == "night":
            return time(23, 0)
        base, hours = {
            "morning": (anchors.morning, 0),
            "midday": (anchors.midday, 0),
            "evening": (anchors.evening, 0),
            "afternoon": (anchors.midday, 1),
            "before_lunch": (anchors.midday, -1),
            "after_work": (anchors.evening, 0),
            "early_morning": (anchors.morning, -2),
            "late_evening": (anchors.evening, 3),
        }.get(name, (anchors.evening, 0))
        hour = min(max(base.hour + hours, 0), 23)
        return time(hour, base.minute)

    # --- clock interpretation ---

    def _times(self, frag: Fragment, *, recurring: bool) -> tuple[list[time], bool]:
        clock = frag.data
        assert isinstance(clock, fr.ClockRef)  # noqa: S101
        hour, minute = clock.hour, clock.minute
        if clock.daypart:
            return [time(_hour_for_daypart(hour, clock.daypart), minute)], False
        if clock.exact:
            return [time(hour % 24, minute)], False
        base = hour % 12
        options = [base, base + 12]
        in_window = [h for h in options if _WINDOW_FROM <= h <= 23]  # noqa: PLR2004
        if recurring:  # conventional reading: 7-11 morning, 12 noon, 1-6 afternoon
            chosen = hour if 7 <= hour <= 12 else hour + 12  # noqa: PLR2004
            return [time(chosen % 24, minute)], True
        return [time(h, minute) for h in in_window or options], True

    # --- date interpretation ---

    def _date_at(self, spec: object, t: time) -> datetime | None:
        if isinstance(spec, fr.DayOffset):
            return datetime.combine(self.today + timedelta(days=spec.days), t)
        if isinstance(spec, fr.CalendarDate):
            day = self._calendar_day(spec)
            return None if day is None else datetime.combine(day, t)
        if isinstance(spec, fr.DayOfMonth):
            return self._day_of_month_at(spec.day, t)
        assert isinstance(spec, fr.WeekdayRef)  # noqa: S101
        return datetime.combine(self._weekday_date(spec, t), t)

    def _day_of_month_at(self, day: int, t: time) -> datetime | None:
        """The nearest ``day`` of a month (this one or a later one) that is still ahead."""
        year, month = self.today.year, self.today.month
        for _ in range(14):  # a 31st needs at most two months to turn up
            if day <= calendar.monthrange(year, month)[1]:
                candidate = datetime.combine(date(year, month, day), t)
                # today's date whose time has passed is reported as such ("сегодня"), not skipped
                if candidate >= self.threshold or candidate.date() == self.today:
                    return candidate
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
        return None

    def _calendar_day(self, spec: fr.CalendarDate) -> date | None:
        if spec.year is not None:
            try:
                return date(spec.year, spec.month, spec.day)
            except ValueError:
                return None
        for year in range(self.today.year, self.today.year + 9):
            if spec.day > calendar.monthrange(year, spec.month)[1]:
                continue
            candidate = date(year, spec.month, spec.day)
            if candidate >= self.today:
                return candidate
        return None

    def _weekday_date(self, spec: fr.WeekdayRef, t: time) -> date:
        if spec.modifier == "next":
            monday_next = self.today - timedelta(days=self.today.weekday()) + timedelta(days=7)
            return monday_next + timedelta(days=spec.weekday)
        sunday = self.today + timedelta(days=6 - self.today.weekday())
        fallback: date | None = None
        for offset in range(15):
            day = self.today + timedelta(days=offset)
            if day.weekday() != spec.weekday or datetime.combine(day, t) < self.threshold:
                continue
            if spec.modifier != "this" or day <= sunday:
                return day
            fallback = fallback or day
        return fallback or self.today

    # --- recurrence ---

    def _recurring(
        self,
        rule_frag: Fragment,
        dates: list[Fragment],
        clocks: list[Fragment],
        dayparts: list[Fragment],
    ) -> None:
        out = self.out
        rule = rule_frag.data
        assert isinstance(rule, fr.RuleRef)  # noqa: S101
        self.use(rule_frag)
        if rule.too_frequent:
            out.flags.add(Flag.RECURRENCE_TOO_FREQUENT)
            return
        until, count = self._limits(dates)
        step = _sub_daily_step(rule)
        if step is not None:
            first = utc_to_local(ctx_now_floor(self.ctx.now) + step, self.ctx.tz)
            self._finish_recurrence(rule, first, self._with_span(first, until), count, None)
            return
        spec = dates[0].data if dates else None
        if isinstance(spec, fr.DayOfMonth):
            if rule.freq is Frequency.MONTHLY and not rule.bymonthday:
                rule = replace(rule, bymonthday=(spec.day,))  # "каждый месяц 15 числа"
            spec = None
        if dates:
            self.use(dates[0])
        if clocks and rule.daypart and isinstance(clocks[0].data, fr.ClockRef):
            clock = clocks[0].data
            if clock.daypart is None and not clock.exact:
                tagged = fr.ClockRef(clock.hour, clock.minute, rule.daypart, True, clock.uncertain)
                clocks = [Fragment(clocks[0].start, clocks[0].end, clocks[0].priority, tagged)]
        if clocks or not rule.daypart:
            times, _ = self._time_options(clocks, dayparts, need_default=True, recurring=True)
        else:
            out.flags.add(Flag.TIME_INFERRED)
            times = [self._anchor(rule.daypart)]
        start = self._first_matching(rule, spec, times[0])
        if start is not None:
            self._finish_recurrence(rule, start, self._with_span(start, until), count, spec)

    def _with_span(self, first: datetime, until: datetime | None) -> datetime | None:
        """An explicit end wins; otherwise "в течение N" counts from the first occurrence."""
        span = self.span
        if until is not None or span is None:
            return until
        if not span.calendar_based:
            return first + timedelta(minutes=span.minutes)
        last_day = first.date() + timedelta(days=span.days) + relativedelta(months=span.months)
        return datetime.combine(last_day - timedelta(days=1), _END_OF_DAY)

    def _first_matching(self, rule: fr.RuleRef, spec: object, t: time) -> datetime | None:
        start_day = self.today
        if isinstance(spec, fr.DayOffset):
            start_day = self.today + timedelta(days=max(spec.days, 0))
        elif isinstance(spec, fr.CalendarDate):
            resolved = self._calendar_day(spec)
            start_day = resolved or self.today
        elif isinstance(spec, fr.WeekdayRef) and rule.freq is not Frequency.WEEKLY:
            start_day = self._weekday_date(spec, t)  # "с понедельника каждый день"
        weekdays = rule.byweekday
        if rule.freq is Frequency.WEEKLY and not weekdays and isinstance(spec, fr.WeekdayRef):
            weekdays = (spec.weekday,)
        for offset in range(_SCAN_DAYS):
            day = start_day + timedelta(days=offset)
            candidate = datetime.combine(day, t)
            if candidate < self.threshold:
                continue
            if _day_matches(rule, weekdays, day, spec):
                return candidate
        return None

    def _limits(self, dates: list[Fragment]) -> tuple[datetime | None, int | None]:
        until: datetime | None = None
        count: int | None = None
        for frag in self.of(fr.UntilEnd):
            assert isinstance(frag.data, fr.UntilEnd)  # noqa: S101
            self.use(frag)
            until = datetime.combine(self._end_of(frag.data.unit), _END_OF_DAY)
        for frag in self.of(fr.CountLimit):
            assert isinstance(frag.data, fr.CountLimit)  # noqa: S101
            self.use(frag)
            count = min(frag.data.count, _MAX_COUNT)
        for frag in self.of(fr.ForSpan)[:1]:
            assert isinstance(frag.data, fr.ForSpan)  # noqa: S101
            self.use(frag)
            self.span = frag.data.delta
        for frag in list(dates):
            if self._is_until_date(frag) and isinstance(frag.data, fr.CalendarDate):
                day = self._calendar_day(frag.data)
                if day is not None:
                    until = datetime.combine(day, _END_OF_DAY)
                    self.out.used.append(frag)
                    dates.remove(frag)
        return until, count

    def _end_of(self, unit: str) -> date:
        if unit == "week":
            return self.today + timedelta(days=6 - self.today.weekday())
        if unit == "month":
            return self.today.replace(day=calendar.monthrange(self.today.year, self.today.month)[1])
        return date(self.today.year, 12, 31)

    def _finish_recurrence(
        self,
        rule: fr.RuleRef,
        first: datetime,
        until: datetime | None,
        count: int | None,
        spec: object,
    ) -> None:
        out = self.out
        if until is not None and until < first:
            out.flags.add(Flag.PAST_UNTIL)
            return
        byweekday = rule.byweekday
        if rule.freq is Frequency.WEEKLY and not byweekday and isinstance(spec, fr.WeekdayRef):
            byweekday = (spec.weekday,)
        bymonth = (first.month,) if rule.freq is Frequency.YEARLY else ()
        try:
            out.recurrence = Recurrence(
                freq=rule.freq,
                interval=rule.interval,
                byweekday=byweekday if rule.freq is Frequency.WEEKLY else (),
                bymonthday=rule.bymonthday if rule.freq is Frequency.MONTHLY else (),
                bymonth=bymonth,
                until_local=until,
                count=count,
            )
        except InvalidRecurrenceError:
            out.flags.add(Flag.RECURRENCE_TOO_FREQUENT)
            return
        out.when = first


def _sub_daily_step(rule: fr.RuleRef) -> timedelta | None:
    if rule.freq is Frequency.HOURLY:
        return timedelta(hours=rule.interval)
    if rule.freq is Frequency.MINUTELY:
        return timedelta(minutes=rule.interval)
    return None


def _day_matches(rule: fr.RuleRef, weekdays: tuple[int, ...], day: date, spec: object) -> bool:
    if rule.freq is Frequency.WEEKLY:
        return not weekdays or day.weekday() in weekdays
    if rule.freq is Frequency.MONTHLY and rule.bymonthday:
        last = calendar.monthrange(day.year, day.month)[1]
        return any((last if d < 0 else min(d, last)) == day.day for d in rule.bymonthday)
    if rule.freq is Frequency.YEARLY and isinstance(spec, fr.CalendarDate):
        last = calendar.monthrange(day.year, spec.month)[1]
        return day.month == spec.month and day.day == min(spec.day, last)
    return True


def _hour_for_daypart(hour: int, daypart: str) -> int:
    h = hour % 24
    if daypart == "morning":
        return h
    if daypart == "day":
        return 12 if h == 12 else (h + 12 if 1 <= h <= 8 else h)  # noqa: PLR2004
    if daypart == "evening":
        return (h + 12) % 24 if 1 <= h <= 12 else h  # noqa: PLR2004
    # night
    if h == 12:  # noqa: PLR2004
        return 0
    return h + 12 if 7 <= h <= 11 else h  # noqa: PLR2004


def ctx_now_floor(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def resolve(chosen: list[Fragment], tokens: list[Token], ctx: ParseContext) -> Resolution:
    return _Resolver(chosen, tokens, ctx).run()
