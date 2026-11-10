"""Rule matchers: tokens -> candidate fragments (possibly overlapping)."""

from __future__ import annotations

import re
from datetime import timedelta

from napominator.domain.recurrence import MIN_INTERVAL, Frequency
from napominator.nlp import fragments as fr
from napominator.nlp.fragments import Fragment
from napominator.nlp.lexicon import (
    CARDINALS,
    DAYPART_GENITIVE,
    DAYPART_INSTRUMENTAL,
    FUNCTION_WORDS,
    MEASURE_WORDS,
    MONTH_ABBR,
    MONTHS,
    ORDINAL_DAYS,
    ORDINAL_HOURS,
    UNITS,
    WEEKDAY_ABBR,
    WEEKDAYS,
    is_lemma,
    lemmas,
    readings,
)
from napominator.nlp.tokenizer import Token

_HOUR_PREPOSITIONS = frozenset({"в", "во", "к", "до", "около"})
_NEAREST = frozenset({"ближайший", "ближайшую", "ближайшее", "ближайшая", "ближайшие"})
_FROM_PREPOSITIONS = frozenset({"с", "со"})
_CLOCK_FILLERS = frozenset({"ровно", "примерно", "приблизительно", "где-то", "точно", "около"})
_DAYPART_SYNONYMS = {"утречком": "morning", "вечерком": "evening"}
# (preposition, noun) -> (anchor name, uncertain): the hour is a guess the user confirms
_DAYPART_PHRASES: dict[tuple[str, str], tuple[str, bool]] = {
    ("после", "обеда"): ("afternoon", True),
    ("перед", "обедом"): ("before_lunch", True),
    ("после", "работы"): ("after_work", True),
    ("к", "вечеру"): ("evening", False),
    ("на", "ночь"): ("night", True),
    ("в", "ночь"): ("night", True),
    ("перед", "сном"): ("night", True),
}
_HOUR_WORDS = frozenset({"час", "часа", "часов", "ч"})
_MINUTE_WORDS = frozenset({"м", "мин"})
_APPROX_UNITS = frozenset({"часа", "дня", "минут", "недели", "месяца", "суток"})  # "часа два"
_DATE_PREPOSITIONS = frozenset({"на", "к", "до", "по"})  # "по 1 декабря": inclusive end
_OBLIQUE_CASES = frozenset({"loct", "loc2", "datv", "ablt", "accs", "gen2"})
_MAX_HOUR = 24
_MAX_MINUTE = 59
_MAX_DAY = 31
_ENDINGS_TENS = range(20, 100, 10)
_FREQUENCY_ADVERBS = {
    "ежедневно": Frequency.DAILY,
    "еженедельно": Frequency.WEEKLY,
    "ежемесячно": Frequency.MONTHLY,
    "ежегодно": Frequency.YEARLY,
    "ежечасно": Frequency.HOURLY,
}
_UNIT_FREQUENCY = {
    "minute": Frequency.MINUTELY,
    "hour": Frequency.HOURLY,
    "day": Frequency.DAILY,
    "week": Frequency.WEEKLY,
    "month": Frequency.MONTHLY,
    "year": Frequency.YEARLY,
}
_DAYPART_NOUNS = {"утро": "morning", "вечер": "evening"}
_CONJUNCTIONS = frozenset({"и", ","})
_DAY_WORDS = {"вчера": -1, "сегодня": 0, "завтра": 1, "послезавтра": 2}
_HOLIDAY_PREPOSITIONS = frozenset({"на", "к", "до"})  # not "с новым годом": that is a greeting
_ABBR_PREPOSITIONS = frozenset({"в", "во", "на", "к", "до", "по", "каждый", "каждую", "и", ","})
_LEAD_MARKERS = frozenset({"заранее", "предупреди", "предупредить", "предупреждай"})


class Scanner:
    """Stateless view over a token list with the matcher entry points."""

    def __init__(self, tokens: list[Token]) -> None:
        self.t = tokens
        self.n = len(tokens)

    # --- token helpers ---

    def text(self, i: int) -> str:
        return self.t[i].text if 0 <= i < self.n else ""

    def word(self, i: int) -> str:
        return self.t[i].text if 0 <= i < self.n and self.t[i].kind == "word" else ""

    def lem(self, i: int, *candidates: str) -> bool:
        w = self.word(i)
        return bool(w) and is_lemma(w, *candidates)

    def number(self, i: int) -> tuple[int, int] | None:
        """Cardinal at ``i`` (digits or words up to 99) -> (value, next index)."""
        if not 0 <= i < self.n:
            return None
        tok = self.t[i]
        if tok.kind == "num" and not tok.ordinal and isinstance(tok.value, int):
            return tok.value, i + 1
        if tok.kind != "word":
            return None
        value = _cardinal_value(tok.text)
        if value is None:
            return None
        if value in _ENDINGS_TENS:
            nxt = self.word(i + 1)
            unit = _cardinal_value(nxt) if nxt else None
            if unit is not None and 1 <= unit <= 9:  # noqa: PLR2004
                return value + unit, i + 2
        return value, i + 1

    def unit(self, i: int) -> str | None:
        w = self.word(i)
        if not w:
            return None
        for lemma in lemmas(w):
            if lemma in UNITS:
                return UNITS[lemma]
        return None

    def duration(self, i: int) -> tuple[fr.Delta, int, str] | None:
        """``20 минут`` / ``час`` / ``пару часов`` / ``полчаса`` -> (delta, next, unit)."""
        special = self._special_duration(i)
        if special is not None:
            return special
        amount: int | None = None
        j = i
        if (num := self.number(i)) is not None:
            amount, j = num
        elif self.lem(i, "пара") and self.word(i) in {"пару", "пары", "пара"}:
            amount, j = 2, i + 1
        unit = self.unit(j)
        if unit is None:
            return None
        if amount is None and self.word(j) in _APPROX_UNITS:  # "через часа два" == 2 часа
            approx = self.number(j + 1)
            if approx is not None and self.unit(approx[1]) is None:
                amount, j = approx[0], approx[1] - 1
        delta = _delta(unit, 1 if amount is None else amount)  # "через час" == 1 час
        end = j + 1
        if unit == "hour" and (bare := self._bare_minutes(end)) is not None:  # "час сорок"
            delta += fr.Delta(minutes=bare[0])
            end = bare[1]
        return delta, end, unit

    def _special_duration(self, i: int) -> tuple[fr.Delta, int, str] | None:
        """``полчаса`` / ``пол часа`` / ``полтора часа`` / ``полгода`` / ``1,5 часа``."""
        if self.lem(i, "полчаса") or self.word(i) in {"полчасика", "получаса"}:
            return fr.Delta(minutes=30), i + 1, "minute"
        if self.word(i) == "пол" and self.lem(i + 1, "час"):
            return fr.Delta(minutes=30), i + 2, "minute"
        if self.lem(i, "полгода"):
            return fr.Delta(months=6), i + 1, "month"
        if self.lem(i, "полтора") and self.unit(i + 1) == "hour":
            return fr.Delta(minutes=90), i + 2, "hour"
        return self._decimal_duration(i)

    def _decimal(self, i: int) -> tuple[int, int] | None:
        """``1,5`` / ``1.5`` -> (tenths, next index); a single decimal digit only."""
        tok = self.t[i] if 0 <= i < self.n else None
        if tok is None:
            return None
        if tok.kind == "date" and isinstance(tok.value, tuple) and tok.value[2] is None:
            if re.fullmatch(r"\d{1,2}\.\d", tok.text):
                return tok.value[0] * 10 + tok.value[1], i + 1
            return None
        if tok.kind == "num" and not tok.ordinal and isinstance(tok.value, int):
            after = self.t[i + 2] if i + 2 < self.n else None
            if self.text(i + 1) == "," and after is not None and re.fullmatch(r"\d", after.text):
                return tok.value * 10 + int(after.text), i + 3
        return None

    def _decimal_duration(self, i: int) -> tuple[fr.Delta, int, str] | None:
        parsed = self._decimal(i)
        if parsed is None:
            return None
        tenths, j = parsed
        unit = self.unit(j)
        if unit not in {"minute", "hour", "day", "week"}:
            return None
        if unit == "minute":
            return None  # a fraction of a minute is not a reminder time
        minutes_per_tenth = {"hour": 6, "day": 144, "week": 1008}[unit]
        total = tenths * minutes_per_tenth
        if unit == "hour":
            return fr.Delta(minutes=total), j + 1, unit
        return fr.Delta(days=total // 1440, minutes=total % 1440), j + 1, unit

    # --- entry point ---

    def scan(self) -> list[Fragment]:
        found: list[Fragment] = []
        for i in range(self.n):
            for matcher in (
                self._category,
                self._recurrence,
                self._limits,
                self._lead,
                self._nag,
                self._delta,
                self._clock,
                self._date,
                self._weekday,
                self._daypart,
            ):
                result = matcher(i)
                if result is not None:
                    found.append(result)
        return found

    # --- category / nag / lead ---

    def _category(self, i: int) -> Fragment | None:
        tok = self.t[i]
        if tok.kind == "tag" and len(tok.text) > 1:
            return Fragment(i, i + 1, fr.P_CATEGORY, fr.CategoryRef(tok.text[1:]))
        if self.lem(i, "категория") and self.word(i + 1) and not self._is_stopword(i + 1):
            start = i - 1 if self.word(i - 1) in {"в", "во"} else i
            return Fragment(start, i + 2, fr.P_CATEGORY, fr.CategoryRef(self.t[i + 1].text))
        return None

    def _is_stopword(self, i: int) -> bool:
        return self.word(i) in FUNCTION_WORDS

    def _nag(self, i: int) -> Fragment | None:
        if self.word(i) in {"настойчиво", "важно"}:
            return Fragment(i, i + 1, fr.P_NAG, fr.NagRef())
        return None

    def _lead(self, i: int) -> Fragment | None:
        if self.word(i) != "за":
            return None
        parsed = self.duration(i + 1)
        if parsed is None:
            return None
        delta, j, first_unit = parsed
        units = {first_unit}
        while True:  # "за 5 дней и 15 минут до", "за 2 часа 30 минут до"
            joined = j + 1 if self.word(j) == "и" else j
            more = self.duration(joined)
            if more is None:
                break
            part, j, unit = more
            delta += part
            units.add(unit)
        with_until = self.word(j) == "до"
        marker = self.word(i - 1) in _LEAD_MARKERS  # "заранее за 2 дня", "предупреди за 2 дня"
        anchored = loose = False
        if with_until:
            if self._starts_event_ref(j + 1):
                anchored = True
                j += 1
            elif j + 1 < self.n and self.t[j + 1].kind in {"word", "num", "tag"}:
                loose = True  # "до встречи": the words stay in the text
            else:
                j += 1  # a bare trailing "до"
        allowed = (
            {"minute", "hour"} if not (with_until or marker) else {"minute", "hour", "day", "week"}
        )
        if not units <= allowed or delta.months:
            return None
        return Fragment(
            i - 1 if marker else i,
            j,
            fr.P_LEAD,
            fr.LeadRef(timedelta(minutes=delta.minutes, days=delta.days), anchored, loose),
        )

    def _starts_event_ref(self, j: int) -> bool:
        """Does an event reference (date, time, weekday, holiday) start at ``j``?

        Deliberately independent of the date/clock matchers: they ask ``_lead_owns_until``.
        """
        if not 0 <= j < self.n:
            return False
        tok = self.t[j]
        if tok.kind in {"date", "time"}:
            return True
        if tok.kind == "num":
            return self._number_starts_event(j)
        if self.word(j) in _DAY_WORDS or self._holiday_len(j):
            return True
        spoken = self._ordinal_day(j)
        if spoken is not None:
            return self._month_at(spoken[1]) is not None or self.lem(spoken[1], "число")
        modifier = self.lem(j, "этот") or self.lem(j, "следующий") or self.lem(j, "будущий")
        return self._weekday_at(j + 1 if modifier else j) is not None

    def _number_starts_event(self, j: int) -> bool:
        """A number after "до": ``17 ноября``, ``15 числа``, ``8 вечера`` (not a count)."""
        tok = self.t[j]
        if tok.ordinal:
            return tok.text.endswith("го") and self._month_at(j + 1) is None
        if self._month_at(j + 1) is not None or self.lem(j + 1, "число"):
            return True
        return self.word(j + 1) in DAYPART_GENITIVE

    def _lead_owns_until(self, k: int) -> bool:
        """Is the "до" at ``k`` the tail of an anchored "за N <unit> до" lead phrase?"""
        if self.word(k) != "до":
            return False
        for start in range(k - 1, max(k - 12, -1), -1):  # room for "за 1 день и 2 часа"
            if self.word(start) == "за":
                frag = self._lead(start)
                if frag is not None and frag.end == k + 1:
                    return True
        return False

    def _until_prefix(self, i: int) -> int:
        """``до пятницы`` / ``с понедельника``: the preposition goes with the date.

        A "до" that belongs to an anchored lead is left alone.
        """
        prev = self.word(i - 1)
        if prev == "до" and not self._lead_owns_until(i - 1):
            return i - 1
        if prev in _FROM_PREPOSITIONS:  # "с понедельника", "начиная со 2 ноября"
            return i - 2 if self.word(i - 2) == "начиная" else i - 1
        return i

    def _date_prefix(self, i: int) -> int:
        """Index where a date fragment starting at ``i`` really begins (with its preposition)."""
        if self.word(i - 1) in _DATE_PREPOSITIONS and not self._lead_owns_until(i - 1):
            return i - 1
        return self._until_prefix(i)

    # --- relative offset ---

    def _delta(self, i: int) -> Fragment | None:
        if self.word(i) != "через":
            return None
        total = fr.Delta()
        j = i + 1
        parts = 0
        while True:
            parsed = self.duration(j)
            if parsed is None:
                break
            delta, j, _ = parsed
            total += delta
            parts += 1
            if self.word(j) == "и" and self.duration(j + 1) is not None:
                j += 1
        if parts == 0:
            return None
        return Fragment(i, j, fr.P_DELTA, total)

    # --- day parts ---

    def _daypart(self, i: int) -> Fragment | None:
        w = self.word(i)
        prev, nxt = self.word(i - 1), self.word(i + 1)
        if w in DAYPART_INSTRUMENTAL or w in _DAYPART_SYNONYMS:
            return self._daypart_word(i, w, prev, nxt)
        phrase = _DAYPART_PHRASES.get((prev, w))  # "после обеда", "к вечеру", "на ночь", ...
        if phrase is not None:
            return Fragment(i - 1, i + 1, fr.P_DAYPART, fr.DayPartRef(*phrase))
        if w == "утра" and prev == "с":
            late = nxt == "пораньше"  # "с утра пораньше"
            ref = fr.DayPartRef("early_morning", True) if late else fr.DayPartRef("morning")
            return Fragment(i - 1, i + (2 if late else 1), fr.P_DAYPART, ref)
        if w == "обед" and prev in {"в", "на"}:
            return Fragment(i - 1, i + 1, fr.P_DAYPART, fr.DayPartRef("midday"))
        if w == "обеденное" and nxt == "время":  # "в обеденное время"
            start = i - 1 if prev == "в" else i
            return Fragment(start, i + 2, fr.P_DAYPART, fr.DayPartRef("midday"))
        return None

    def _daypart_word(self, i: int, w: str, prev: str, nxt: str) -> Fragment | None:
        """``утром`` / ``вечерком`` / ``рано утром`` / ``поздно вечером`` / ``ночью``."""
        part = _DAYPART_SYNONYMS.get(w) or DAYPART_INSTRUMENTAL[w]
        if part == "morning" and prev == "рано":
            return Fragment(i - 1, i + 1, fr.P_DAYPART, fr.DayPartRef("early_morning", True))
        if part == "morning" and nxt == "пораньше":
            return Fragment(i, i + 2, fr.P_DAYPART, fr.DayPartRef("early_morning", True))
        if part == "evening" and prev == "поздно":
            return Fragment(i - 1, i + 1, fr.P_DAYPART, fr.DayPartRef("late_evening", True))
        if part == "evening" and nxt in {"попозже", "позже"}:
            return Fragment(i, i + 2, fr.P_DAYPART, fr.DayPartRef("late_evening", True))
        if part == "night":
            return Fragment(i, i + 1, fr.P_DAYPART, fr.DayPartRef("night", True))
        name = {"day": "midday"}.get(part, part)
        return Fragment(i, i + 1, fr.P_DAYPART, fr.DayPartRef(name))

    # --- clock ---

    def _clock(self, i: int) -> Fragment | None:
        frag = self._clock_core(i)
        if frag is None:
            return None
        start = frag.start
        while self.word(start - 1) in _CLOCK_FILLERS:  # "ровно в 9", "примерно в 9"
            start -= 1
        if start == frag.start:
            return frag
        return Fragment(start, frag.end, frag.priority, frag.data)

    def _clock_core(self, i: int) -> Fragment | None:
        if self.t[i].kind == "time":
            return self._explicit_time(i)
        w = self.word(i)
        if w == "без":
            return self._minus_minutes(i)
        if w == "на":  # "на 9 утра": only with an explicit part of the day
            frag = self._hour_number(i)
            has_part = frag is not None and getattr(frag.data, "daypart", None) is not None
            return frag if has_part else None
        if w in _HOUR_PREPOSITIONS:
            frag = self._after_preposition(i)
            if frag is not None and w == "до" and self._lead_owns_until(i):
                return Fragment(i + 1, frag.end, frag.priority, frag.data)  # "до" is the lead's
            return frag
        return None

    def _after_preposition(self, i: int) -> Fragment | None:
        for lemma, hour in (("полдень", 12), ("полночь", 0)):
            if self.lem(i + 1, lemma):
                return Fragment(i, i + 2, fr.P_CLOCK, fr.ClockRef(hour, 0, None, exact=True))
        for lemma, minute in (("половина", 30), ("четверть", 15)):
            if self.lem(i + 1, lemma):
                return self._fraction(i, minute)
        if (pol := self._half_past(i)) is not None:
            return pol
        return self._hour_number(i)

    def _half_past(self, i: int) -> Fragment | None:
        """``в пол девятого`` / ``в полдевятого`` / ``в пол-девятого`` -> 8:30."""
        word = self.word(i + 1)
        if word == "пол":
            ordinal, end = _ordinal_hour(self.word(i + 2)), i + 3
        elif word.startswith("пол") and len(word) > 4:  # noqa: PLR2004
            ordinal, end = _ordinal_hour(word[3:].lstrip("-")), i + 2
        else:
            return None
        if ordinal is None:
            return None
        return self._clock_with_part(i, end, ordinal - 1 or 12, 30)

    def _clock_with_part(self, start: int, end: int, hour: int, minute: int) -> Fragment:
        part = DAYPART_GENITIVE.get(self.word(end)) or DAYPART_INSTRUMENTAL.get(self.word(end))
        if part:
            end += 1
        return Fragment(
            start, end, fr.P_CLOCK, fr.ClockRef(hour, minute, part, exact=part is not None)
        )

    def _explicit_time(self, i: int) -> Fragment | None:
        tok = self.t[i]
        assert isinstance(tok.value, tuple)  # noqa: S101
        hour, minute, _ = tok.value
        if hour > 23 or minute > _MAX_MINUTE:  # noqa: PLR2004
            return None
        prep = self.word(i - 1)
        takes_prep = prep in _HOUR_PREPOSITIONS or prep == "на"
        start = i - 1 if takes_prep and not self._lead_owns_until(i - 1) else i
        end = i + 1
        part = DAYPART_GENITIVE.get(self.word(end)) or DAYPART_INSTRUMENTAL.get(self.word(end))
        if part:
            end += 1
        return Fragment(start, end, fr.P_CLOCK, fr.ClockRef(hour, minute, part, exact=True))

    def _fraction(self, i: int, minute: int) -> Fragment | None:
        """``в половине десятого`` -> 9:30, ``в четверть восьмого`` -> 7:15."""
        hour_word = self.word(i + 2)
        ordinal = _ordinal_hour(hour_word) if hour_word else None
        if ordinal is None:
            return None
        end = i + 3
        part = DAYPART_GENITIVE.get(self.word(end)) or DAYPART_INSTRUMENTAL.get(self.word(end))
        if part:
            end += 1
        hour = ordinal - 1 or 12
        return Fragment(i, end, fr.P_CLOCK, fr.ClockRef(hour, minute, part, exact=part is not None))

    def _minus_minutes(self, i: int) -> Fragment | None:
        """``без пятнадцати три`` -> 2:45, ``без четверти три`` -> 2:45."""
        j = i + 1
        if self.lem(j, "четверть"):
            minutes, j = 15, j + 1
        else:
            num = self.number(j)
            if num is None or not 1 <= num[0] <= _MAX_MINUTE:
                return None
            minutes, j = num
            if self.lem(j, "минута"):
                j += 1
        hour_num = self.number(j)
        if hour_num is None or not 1 <= hour_num[0] <= 12:  # noqa: PLR2004
            return None
        hour, end = hour_num
        part = DAYPART_GENITIVE.get(self.word(end)) or DAYPART_INSTRUMENTAL.get(self.word(end))
        if part:
            end += 1
        return Fragment(
            i,
            end,
            fr.P_CLOCK,
            fr.ClockRef((hour - 1) or 12, 60 - minutes, part, exact=part is not None),
        )

    def _hour_number(self, i: int) -> Fragment | None:
        num = self.number(i + 1)
        if num is None:
            return None
        hour, j = num
        if hour > _MAX_HOUR or self._month_at(j) is not None:  # "в 5 мая" is a date
            return None
        if self.lem(j, "число") and self.word(j) in {"числа", "число"}:  # "до 15 числа"
            return None
        minute = 0
        hour_word = False
        if self.word(j) in _HOUR_WORDS:
            hour_word = True
            j += 1
            minutes = self.number(j)
            if minutes is not None and self._is_minute_word(minutes[1]) and minutes[0] <= 59:  # noqa: PLR2004
                minute = minutes[0]
                j = minutes[1] + 1
            elif (bare := self._bare_minutes(j)) is not None:  # "в 8 часов 30"
                minute, j = bare
        elif (bare := self._bare_minutes(j)) is not None:  # "в 8 30", "в семь тридцать"
            minute, j = bare
        part = DAYPART_GENITIVE.get(self.word(j)) or DAYPART_INSTRUMENTAL.get(self.word(j))
        if part:
            j += 1
        uncertain = False
        if not hour_word and part is None:
            verdict = self._what_follows(hour, j)
            if verdict == "countable":
                return None
            uncertain = verdict == "doubt"
        exact = part is not None or hour == 0 or hour > 12  # noqa: PLR2004
        return Fragment(
            i, j, fr.P_CLOCK, fr.ClockRef(hour % 24, minute, part, exact=exact, uncertain=uncertain)
        )

    def _is_minute_word(self, k: int) -> bool:
        return self.lem(k, "минута") or self.word(k) in _MINUTE_WORDS

    def _bare_minutes(self, j: int) -> tuple[int, int] | None:
        """Minutes written right after the hour without a unit: ``30`` / ``05`` / ``тридцать``."""
        parsed = self._minutes_number(j)
        if parsed is None:
            return None
        minute, end = parsed
        if self._month_at(end) is not None or self.lem(end, "число"):
            return None  # "в 5 20 апреля", "в 9 15 числа": the number is a day
        if self._is_minute_word(end) or self.lem(end, "час"):
            return None
        if self._what_follows(minute, end) == "countable":
            return None  # "в 8 30 человек"
        return minute, end

    def _minutes_number(self, j: int) -> tuple[int, int] | None:
        if not 0 <= j < self.n:
            return None
        tok = self.t[j]
        if tok.kind == "num":
            value = tok.value
            if tok.ordinal or not isinstance(value, int) or len(tok.text) != 2:  # noqa: PLR2004
                return None
            return (value, j + 1) if value <= _MAX_MINUTE else None
        if self.word(j) in {"ноль", "нуль"}:  # "ноль пять" -> 05
            num = self.number(j + 1)
            return num if num is not None and num[0] <= 9 else None  # noqa: PLR2004
        num = self.number(j)
        return num if num is not None and 10 <= num[0] <= _MAX_MINUTE else None  # noqa: PLR2004

    def _what_follows(self, amount: int, j: int) -> str:
        """Is "в N <next>" a time, a counted noun ("в 5 магазинов") or in doubt?"""
        if j >= self.n or self.t[j].kind != "word":
            return "time"
        word = self.t[j].text
        if word in FUNCTION_WORDS:
            return "time"
        found = readings(word)
        if amount % 100 in range(11, 15):
            forms = {("gent", "plur")}
        elif amount % 10 == 1:
            forms = {("nomn", "sing")}
        elif amount % 10 in (2, 3, 4):
            forms = {("gent", "sing")}
        else:
            forms = {("gent", "plur")}
        if any(r.pos == "NOUN" and (r.case, r.number) in forms for r in found):
            return "countable"
        if found and found[0].pos == "NOUN" and found[0].case in _OBLIQUE_CASES:
            return "doubt"
        return "time"

    # --- dates ---

    def _date(self, i: int) -> Fragment | None:
        tok = self.t[i]
        if tok.kind == "date":
            return self._dotted_date(i)
        if tok.kind == "num":
            return self._day_and_month(i) or self._day_of_month(i)
        return (
            self._day_word(i)
            or self._holiday(i)
            or self._day_phrase(i)
            or self._day_and_month(i)
            or self._day_of_month(i)
        )

    def _day_phrase(self, i: int) -> Fragment | None:
        """``с завтрашнего дня`` / ``со следующей недели`` (a start date of a repeat)."""
        for lemma, days in (("завтрашний", 1), ("сегодняшний", 0)):
            if self.lem(i, lemma) and self.lem(i + 1, "день"):
                return Fragment(self._until_prefix(i), i + 2, fr.P_DATE, fr.DayOffset(days))
        if (
            (self.lem(i, "следующий") or self.lem(i, "будущий"))
            and self.lem(i + 1, "неделя")
            and self.word(i - 1) in _FROM_PREPOSITIONS
        ):
            return Fragment(self._until_prefix(i), i + 2, fr.P_DATE, fr.WeekdayRef(0, "next"))
        return None

    def _day_number(self, i: int) -> tuple[int, int] | None:
        """A day of the month written as ``17`` / ``17-го`` / ``семнадцатого`` -> (day, next)."""
        tok = self.t[i]
        if tok.kind == "num":
            if isinstance(tok.value, int) and 1 <= tok.value <= _MAX_DAY:
                return tok.value, i + 1
            return None
        return self._ordinal_day(i)

    def _ordinal_day(self, i: int) -> tuple[int, int] | None:
        """Spoken genitive day: ``пятого`` -> 5, ``двадцать третьего`` -> 23."""
        w = self.word(i)
        if w in {"двадцать", "тридцать"}:
            unit = self._ordinal_value(i + 1)
            tens = 20 if w == "двадцать" else 30
            if unit is not None and 1 <= unit <= 9 and (tens == 20 or unit == 1):  # noqa: PLR2004
                return tens + unit, i + 2
            return None
        value = self._ordinal_value(i)
        return None if value is None else (value, i + 1)

    def _ordinal_value(self, i: int) -> int | None:
        w = self.word(i)
        if not w or not any(r.case == "gent" for r in readings(w)):
            return None
        for lemma in lemmas(w):
            if lemma in ORDINAL_DAYS:
                return ORDINAL_DAYS[lemma]
        return None

    def _day_of_month(self, i: int) -> Fragment | None:
        """``15 числа`` / ``15-го`` / ``пятнадцатого числа`` -> next such day, month not named."""
        tok = self.t[i]
        number = self._day_number(i)
        if number is None:
            return None
        day, after = number
        if self._month_at(after) is not None:
            return None
        if self.lem(after, "число") and self.word(after) in {"числа", "число"}:
            end = after + 1
        elif tok.kind == "num" and tok.ordinal and tok.text.endswith("го"):
            end = after
        else:
            return None
        return Fragment(self._date_prefix(i), end, fr.P_DATE, fr.DayOfMonth(day))

    def _holiday_len(self, i: int) -> int:
        """Tokens taken by a fixed-date holiday at ``i`` (0 if none): "новый год"."""
        if self.lem(i, "новый") and self.lem(i + 1, "год"):
            return 2
        return 0

    def _holiday(self, i: int) -> Fragment | None:
        """``на новый год`` / ``до нового года`` -> 1 января (a midnight moment)."""
        prep = self.word(i - 1)
        if not self._holiday_len(i):
            return None
        # "в новый год" (accusative) is the holiday itself, "в новом году" is a whole year
        if prep not in _HOLIDAY_PREPOSITIONS and not (
            prep in {"в", "во"} and self.word(i) == "новый"
        ):
            return None
        start = i if self._lead_owns_until(i - 1) else i - 1
        return Fragment(start, i + 2, fr.P_DATE, fr.CalendarDate(1, 1, None, at_midnight=True))

    def _day_word(self, i: int) -> Fragment | None:
        w = self.word(i)
        if w not in _DAY_WORDS:
            return None
        start = i - 1 if self.word(i - 1) == "на" and w == "завтра" else self._until_prefix(i)
        return Fragment(start, i + 1, fr.P_DATE, fr.DayOffset(_DAY_WORDS[w]))

    def _dotted_date(self, i: int) -> Fragment | None:
        value = self.t[i].value
        if not isinstance(value, tuple):
            return None
        day, month, year = value
        if not (1 <= day <= _MAX_DAY and 1 <= month <= 12):  # noqa: PLR2004
            return None
        if self.word(i + 1) in MEASURE_WORDS or self.text(i + 1) == "%":
            return None  # "3.5 кг"
        if self.word(i - 1) in {"через", "за"} and self.unit(i + 1) is not None:
            return None  # "через 1.5 часа"
        return Fragment(self._date_prefix(i), i + 1, fr.P_DATE, fr.CalendarDate(day, month, year))

    def _day_and_month(self, i: int) -> Fragment | None:
        number = self._day_number(i)
        if number is None:
            return None
        value, after = number
        month = self._month_at(after)
        if month is None:
            return None
        end = after + 1
        year: int | None = None
        year_num = self.number(end)
        if year_num is not None and 1900 < year_num[0] < 2200:  # noqa: PLR2004
            year, end = year_num
            if self.word(end) in {"года", "год", "г"}:
                end += 1
        return Fragment(self._date_prefix(i), end, fr.P_DATE, fr.CalendarDate(value, month, year))

    def _month_at(self, i: int) -> int | None:
        w = self.word(i)
        if not w:
            return None
        for lemma in lemmas(w):
            if lemma in MONTHS:
                return MONTHS[lemma]
        if w in MONTH_ABBR and i > 0 and self.t[i - 1].kind == "num":  # "17 нояб"
            return MONTH_ABBR[w]
        return None

    def _weekday(self, i: int) -> Fragment | None:
        weekday = self._weekday_at(i)
        if weekday is None:
            if self.lem(i, "выходной") and self.word(i - 1) in {"в", "на"}:
                return Fragment(i - 1, i + 1, fr.P_DATE, fr.WeekdayRef(5))
            return None
        modifier = ""
        start = i
        end = i + 1
        if self.lem(i - 1, "этот"):
            modifier, start = "this", i - 1
        elif self.lem(i - 1, "следующий") or self.lem(i - 1, "будущий"):
            modifier, start = "next", i - 1
        elif self.word(i - 1) in _NEAREST:  # the nearest one, as with no modifier at all
            start = i - 1
        if self.word(start - 1) in {"в", "во", "на", "к"}:
            start -= 1
        else:
            start = self._until_prefix(start)
        if not modifier:
            # "на следующей неделе в среду" / "в среду на следующей неделе"
            before = self._week_phrase(start - 3)
            after = self._week_phrase(i + 1)
            if before:
                modifier, start = before, start - 3
            elif after:
                modifier, end = after, i + 4
        return Fragment(start, end, fr.P_DATE, fr.WeekdayRef(weekday, modifier))

    def _week_phrase(self, a: int) -> str:
        """``на следующей неделе`` -> "next", ``на этой неделе`` -> "this", else ""."""
        if self.word(a) != "на" or not self.lem(a + 2, "неделя"):
            return ""
        if self.lem(a + 1, "следующий") or self.lem(a + 1, "будущий"):
            return "next"
        return "this" if self.lem(a + 1, "этот") else ""

    def _weekday_at(self, i: int) -> int | None:
        w = self.word(i)
        if not w:
            return None
        for lemma in lemmas(w):
            if lemma in WEEKDAYS:
                return WEEKDAYS[lemma]
        if w in WEEKDAY_ABBR and (
            self.word(i - 1) in _ABBR_PREPOSITIONS or self.text(i - 1) == ","
        ):
            return WEEKDAY_ABBR[w]  # "в пт", "по пн и ср"
        return None

    # --- limits ---

    def _limits(self, i: int) -> Fragment | None:
        span = self._for_span(i)
        if span is not None:
            return span
        if self.word(i) == "до" and self.lem(i + 1, "конец") and self.word(i + 1) == "конца":
            for unit in ("неделя", "месяц", "год"):
                if self.lem(i + 2, unit):
                    return Fragment(i, i + 3, fr.P_LIMIT, fr.UntilEnd(_UNIT_NAMES[unit]))
        tok = self.t[i]
        if (
            tok.kind == "num"
            and not tok.ordinal
            and isinstance(tok.value, int)
            and self.word(i + 1) == "раз"
            and tok.value >= 1
        ):
            return Fragment(i, i + 2, fr.P_LIMIT, fr.CountLimit(tok.value))
        return None

    def _for_span(self, i: int) -> Fragment | None:
        """``в течение недели`` / ``на протяжении 5 дней`` / ``в течение месяца``."""
        w, nxt = self.word(i), self.word(i + 1)
        if not ((w == "в" and nxt == "течение") or (w == "на" and nxt == "протяжении")):
            return None
        parsed = self.duration(i + 2)
        if parsed is None:
            return None
        delta, end, _ = parsed
        return Fragment(i, end, fr.P_LIMIT, fr.ForSpan(delta))

    # --- recurrence ---

    def _recurrence(self, i: int) -> Fragment | None:
        w = self.word(i)
        if w:
            found = self._word_recurrence(i, w)
            if found is not None:
                return found
        if self.t[i].kind == "num":
            return self._day_of_every_month(i)
        return None

    def _word_recurrence(self, i: int, w: str) -> Fragment | None:
        if w in _FREQUENCY_ADVERBS:
            return _rule(i, i + 1, _FREQUENCY_ADVERBS[w])
        if is_lemma(w, "каждый"):
            return self._nth_weekday_of_month(i) or self._after_every(i)
        if w == "раз" and self.word(i + 1) in {"в", "во"}:
            return self._once_per(i)
        if w in {"по", "в", "во"}:  # "по будням" / "в будни"
            return self._by_weekdays(i) if w == "по" else self._in_weekdays(i)
        if self.lem(i, "последний"):
            return self._last_day_of_month(i)
        return None

    def _in_weekdays(self, i: int) -> Fragment | None:
        """``в будни`` / ``в будние дни`` is a habit (every weekday), not a single day."""
        j = i + 1
        if self.lem(j, "будни"):
            return _rule(i, j + 1, Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4))
        if self.lem(j, "будний") and self.lem(j + 1, "день"):
            return _rule(i, j + 2, Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4))
        return self._nth_weekday_of_month(i)

    def _nth_weekday_of_month(self, i: int) -> Fragment | None:
        """``в последнюю пятницу месяца``: recognised, but there is no such repeat rule."""
        if self._weekday_at(i + 2) is None or not self.lem(i + 3, "месяц"):
            return None
        if not any(self.lem(i + 1, o) for o in ("последний", "первый", "второй", "третий")):
            return None
        return Fragment(i, i + 4, fr.P_RECURRENCE, fr.Unsupported())

    def _last_day_of_month(self, i: int) -> Fragment | None:
        if not (self.lem(i + 1, "день") and self.lem(i + 2, "месяц")):
            return None
        start = i - 1 if self.word(i - 1) in {"в", "на"} else i
        return _rule(start, i + 3, Frequency.MONTHLY, bymonthday=(-1,))

    def _day_of_every_month(self, i: int) -> Fragment | None:
        """``1 числа каждого месяца`` / ``15-го числа каждый месяц``."""
        tok = self.t[i]
        assert isinstance(tok.value, int)  # noqa: S101
        if not 1 <= tok.value <= _MAX_DAY:
            return None
        if self.lem(i + 1, "число") and self.lem(i + 2, "каждый") and self.lem(i + 3, "месяц"):
            return _rule(i, i + 4, Frequency.MONTHLY, bymonthday=(tok.value,))
        return None

    def _after_every(self, i: int) -> Fragment | None:  # noqa: PLR0911
        j = i + 1
        if self.lem(j, "будний") and self.lem(j + 1, "день"):
            return _rule(i, j + 2, Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4))
        if self.lem(j, "выходной"):
            return _rule(i, j + 1, Frequency.WEEKLY, byweekday=(5, 6))
        part = _daypart_noun(self.word(j))
        if part:
            return _rule(i, j + 1, Frequency.DAILY, daypart=part)
        weekdays, end = self._weekday_list(j)
        if weekdays:
            return _rule(i, end, Frequency.WEEKLY, byweekday=weekdays)
        tok = self.t[j] if j < self.n else None
        if tok is not None and tok.kind == "num" and isinstance(tok.value, int):
            if self.lem(j + 1, "число"):
                return _rule(i, j + 2, Frequency.MONTHLY, bymonthday=(tok.value,))
            if tok.ordinal and 1 <= tok.value <= _MAX_DAY:
                return _rule(i, j + 1, Frequency.MONTHLY, bymonthday=(tok.value,))
        parsed = self.duration(j)
        if parsed is None:
            return None
        delta, end, unit = parsed
        return self._rule_from_duration(i, end, delta, unit)

    def _once_per(self, i: int) -> Fragment | None:
        parsed = self.duration(i + 2)
        if parsed is None:
            return None
        delta, end, unit = parsed
        return self._rule_from_duration(i, end, delta, unit)

    def _rule_from_duration(
        self, start: int, end: int, delta: fr.Delta, unit: str
    ) -> Fragment | None:
        if delta.months:
            freq = Frequency.YEARLY if unit == "year" else Frequency.MONTHLY
            interval = delta.months // 12 if unit == "year" else delta.months
            return _rule(start, end, freq, interval=interval)
        if delta.days:
            if unit == "week":
                return _rule(start, end, Frequency.WEEKLY, interval=delta.days // 7)
            return _rule(start, end, Frequency.DAILY, interval=delta.days)
        minutes = delta.minutes
        too_frequent = timedelta(minutes=minutes) < MIN_INTERVAL
        if minutes % 60 == 0:
            return _rule(start, end, Frequency.HOURLY, interval=minutes // 60)
        return _rule(start, end, Frequency.MINUTELY, interval=minutes, too_frequent=too_frequent)

    def _by_weekdays(self, i: int) -> Fragment | None:
        j = i + 1
        if self.lem(j, "будни"):
            return _rule(i, j + 1, Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4))
        if self.lem(j, "выходной"):
            return _rule(i, j + 1, Frequency.WEEKLY, byweekday=(5, 6))
        weekdays, end = self._weekday_list(j)
        if weekdays:
            return _rule(i, end, Frequency.WEEKLY, byweekday=weekdays)
        return None

    def _weekday_list(self, i: int) -> tuple[tuple[int, ...], int]:
        """``вторник и четверг`` / ``понедельник, среду и пятницу``."""
        days: list[int] = []
        j = i
        while True:
            day = self._weekday_at(j)
            if day is None:
                break
            days.append(day)
            j += 1
            if self.text(j) in _CONJUNCTIONS and self._weekday_at(j + 1) is not None:
                j += 1
            else:
                break
        return tuple(sorted(set(days))), j


_UNIT_NAMES = {"неделя": "week", "месяц": "month", "год": "year"}


def _rule(
    start: int,
    end: int,
    freq: Frequency,
    *,
    interval: int = 1,
    byweekday: tuple[int, ...] = (),
    bymonthday: tuple[int, ...] = (),
    daypart: str | None = None,
    too_frequent: bool = False,
) -> Fragment:
    rule = fr.RuleRef(
        freq=freq,
        interval=max(interval, 1),
        byweekday=byweekday,
        bymonthday=bymonthday,
        daypart=daypart,
        too_frequent=too_frequent,
    )
    return Fragment(start, end, fr.P_RECURRENCE, rule)


def _delta(unit: str, amount: int) -> fr.Delta:
    if unit == "minute":
        return fr.Delta(minutes=amount)
    if unit == "hour":
        return fr.Delta(minutes=amount * 60)
    if unit == "day":
        return fr.Delta(days=amount)
    if unit == "week":
        return fr.Delta(days=amount * 7)
    if unit == "month":
        return fr.Delta(months=amount)
    return fr.Delta(months=amount * 12)


def _cardinal_value(word: str) -> int | None:
    for lemma in lemmas(word):
        if lemma in CARDINALS:
            return CARDINALS[lemma]
    return None


def _ordinal_hour(word: str) -> int | None:
    for lemma in lemmas(word):
        if lemma in ORDINAL_HOURS:
            return ORDINAL_HOURS[lemma]
    return None


def _daypart_noun(word: str) -> str | None:
    for lemma in lemmas(word):
        if lemma in _DAYPART_NOUNS:
            return _DAYPART_NOUNS[lemma]
    return None
