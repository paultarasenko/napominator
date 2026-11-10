"""Morphology access and the closed-class vocabulary used by the rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pymorphy3

# pymorphy3 crashes on letters that have no Unicode name (e.g. rare scripts), so only
# Cyrillic/Latin words are analysed; everything else is treated as an unknown word.
_ANALYSABLE = re.compile(r"^[а-яеa-z]+(?:-[а-яеa-z]+)*$")


@lru_cache(maxsize=1)
def _analyzer() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


@dataclass(frozen=True, slots=True)
class Reading:
    pos: str | None
    case: str | None
    number: str | None


@lru_cache(maxsize=65536)
def lemmas(word: str) -> frozenset[str]:
    """All candidate lemmas of ``word`` (``ё`` folded to ``е``)."""
    if not _ANALYSABLE.match(word):
        return frozenset({word})
    return frozenset(p.normal_form.replace("ё", "е") for p in _analyzer().parse(word))


@lru_cache(maxsize=65536)
def readings(word: str) -> tuple[Reading, ...]:
    """Morphological readings, most probable first (none for words pymorphy3 can't analyse)."""
    if not _ANALYSABLE.match(word):
        return ()
    return tuple(Reading(p.tag.POS, p.tag.case, p.tag.number) for p in _analyzer().parse(word)[:6])


def genitive_to_nominative(word: str) -> str | None:
    """``встречи`` -> ``встреча`` for a noun in the genitive; ``None`` when it is not one."""
    if not _ANALYSABLE.match(word):
        return None
    top = _analyzer().parse(word)[0]
    if top.tag.POS != "NOUN" or top.tag.case != "gent":
        return None
    nominative = top.inflect({"nomn", top.tag.number or "sing"})
    return None if nominative is None else str(nominative.word)


def is_lemma(word: str, *candidates: str) -> bool:
    return not lemmas(word).isdisjoint(candidates)


WEEKDAYS: dict[str, int] = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
    "воскресение": 6,
}

MONTHS: dict[str, int] = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}

# Abbreviations only count after a preposition / a day number (see the matchers).
WEEKDAY_ABBR: dict[str, int] = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}
MONTH_ABBR: dict[str, int] = {
    "янв": 1,
    "фев": 2,
    "февр": 2,
    "мар": 3,
    "апр": 4,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "сент": 9,
    "окт": 10,
    "ноя": 11,
    "нояб": 11,
    "дек": 12,
}

UNITS: dict[str, str] = {
    "минута": "minute",
    "мин": "minute",
    "час": "hour",
    "часик": "hour",
    "часок": "hour",
    "ч": "hour",
    "минутка": "minute",
    "минуточка": "minute",
    "денек": "day",
    "день": "day",
    "сутки": "day",
    "неделя": "week",
    "месяц": "month",
    "год": "year",
}

CARDINALS: dict[str, int] = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
    "двое": 2,  # "через двое суток"
    "трое": 3,
    "четверо": 4,
}

ORDINAL_HOURS: dict[str, int] = {
    "первый": 1,
    "второй": 2,
    "третий": 3,
    "четвертый": 4,
    "пятый": 5,
    "шестой": 6,
    "седьмой": 7,
    "восьмой": 8,
    "девятый": 9,
    "десятый": 10,
    "одиннадцатый": 11,
    "двенадцатый": 12,
}

# Spoken day of the month ("пятого ноября"); 21-29 and 31 are built from tens + unit.
ORDINAL_DAYS: dict[str, int] = {
    "первый": 1,
    "второй": 2,
    "третий": 3,
    "четвертый": 4,
    "пятый": 5,
    "шестой": 6,
    "седьмой": 7,
    "восьмой": 8,
    "девятый": 9,
    "десятый": 10,
    "одиннадцатый": 11,
    "двенадцатый": 12,
    "тринадцатый": 13,
    "четырнадцатый": 14,
    "пятнадцатый": 15,
    "шестнадцатый": 16,
    "семнадцатый": 17,
    "восемнадцатый": 18,
    "девятнадцатый": 19,
    "двадцатый": 20,
    "тридцатый": 30,
}

DAYPART_GENITIVE = {"утра": "morning", "дня": "day", "вечера": "evening", "ночи": "night"}
DAYPART_INSTRUMENTAL = {"утром": "morning", "днем": "day", "вечером": "evening", "ночью": "night"}

PREPOSITIONS = frozenset({"в", "во", "на", "к", "до", "после", "через", "за", "по", "с", "у", "от"})
FUNCTION_WORDS = frozenset(
    {"и", "а", "но", "или", "же", "бы", "ли", "не", "для", "из", "над", "под", "при", "про"}
    | PREPOSITIONS
)
EDGE_WORDS = frozenset({"и", "а", "но", "или"} | PREPOSITIONS)
MEASURE_WORDS = frozenset(
    {"кг", "г", "гр", "л", "мл", "м", "см", "мм", "км", "шт", "руб", "р", "т", "%", "грамм"}
)
