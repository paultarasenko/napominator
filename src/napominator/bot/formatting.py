"""Russian formatting helpers: dates, repeat rules, plurals. Pure functions."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from html import escape

from napominator.domain.recurrence import Frequency, Recurrence

MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
MONTHS_NOM = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)  # fmt: skip
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_NOM = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
WEEKDAYS_DAT_PL = (
    "понедельникам",
    "вторникам",
    "средам",
    "четвергам",
    "пятницам",
    "субботам",
    "воскресеньям",
)
_RELATIVE_DAYS = {0: "сегодня", 1: "завтра", 2: "послезавтра"}
_WORKDAYS = (0, 1, 2, 3, 4)
_WEEKEND = (5, 6)
_MINUTES_IN_HOUR = 60


def h(text: str) -> str:
    """Escape user text for Telegram HTML."""
    return escape(text, quote=False)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 100 in range(11, 15):
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"


def clock(moment: datetime | time) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def date_part(local: datetime, now_local: datetime) -> str:
    text = f"{local.day} {MONTHS_GEN[local.month - 1]}"
    return text if local.year == now_local.year else f"{text} {local.year}"


def describe_moment(local: datetime, now_local: datetime) -> str:
    """Full, unambiguous time for confirmation cards: ``завтра, 14 октября в 18:00``."""
    days = (local.date() - now_local.date()).days
    lead = _RELATIVE_DAYS.get(days) or WEEKDAYS_SHORT[local.weekday()]
    return f"{lead}, {date_part(local, now_local)} в {clock(local)}"


def short_moment(local: datetime, now_local: datetime) -> str:
    """Compact time for list rows: ``18:00`` today, ``завтра 09:00``, ``пт 16.10 10:00``."""
    days = (local.date() - now_local.date()).days
    if days == 0:
        return clock(local)
    if days in _RELATIVE_DAYS:
        return f"{_RELATIVE_DAYS[days]} {clock(local)}"
    return f"{WEEKDAYS_SHORT[local.weekday()]} {local.day:02d}.{local.month:02d} {clock(local)}"


def describe_delta(delta: timedelta) -> str:
    """``через 20 минут`` / ``через 2 ч 15 мин`` / ``через 3 дня``."""
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < _MINUTES_IN_HOUR:
        return "через " + count(minutes, "минуту", "минуты", "минут")
    hours, rest = divmod(minutes, _MINUTES_IN_HOUR)
    if hours < 24:  # noqa: PLR2004
        tail = f" {rest} мин" if rest else ""
        return f"через {hours} ч{tail}"
    return "через " + count(hours // 24, "день", "дня", "дней")


def _weekday_list(days: tuple[int, ...], names: tuple[str, ...]) -> str:
    chosen = [names[d] for d in sorted(set(days))]
    return chosen[0] if len(chosen) == 1 else ", ".join(chosen[:-1]) + " и " + chosen[-1]


def describe_rule(rule: Recurrence | None) -> str:  # noqa: PLR0911
    """Human description of a repeat rule (empty string for a one-off)."""
    if rule is None:
        return ""
    n = rule.interval
    match rule.freq:
        case Frequency.MINUTELY:
            return "каждые " + count(n, "минуту", "минуты", "минут")
        case Frequency.HOURLY:
            return "каждый час" if n == 1 else "каждые " + count(n, "час", "часа", "часов")
        case Frequency.DAILY:
            return "каждый день" if n == 1 else "каждые " + count(n, "день", "дня", "дней")
        case Frequency.WEEKLY:
            days = tuple(sorted(set(rule.byweekday)))
            if n == 1 and days == _WORKDAYS:
                return "по будням"
            if n == 1 and days == _WEEKEND:
                return "по выходным"
            if n == 1 and days:
                return "по " + _weekday_list(days, WEEKDAYS_DAT_PL)
            base = "каждую неделю" if n == 1 else "каждые " + count(n, "неделю", "недели", "недель")
            return base + (", " + _weekday_list(days, WEEKDAYS_SHORT) if days else "")
        case Frequency.MONTHLY:
            if rule.bymonthday == (-1,):
                return "в последний день месяца"
            base = "каждый месяц" if n == 1 else "каждые " + count(n, "месяц", "месяца", "месяцев")
            if rule.bymonthday:
                days_text = ", ".join(str(d) for d in rule.bymonthday)
                return f"{base}, {days_text}-го числа"
            return base
        case _:
            return "каждый год" if n == 1 else "каждые " + count(n, "год", "года", "лет")


def lead_phrase(minutes: int) -> str:
    """How long before the event a heads-up arrives: ``час``, ``2 часа``, ``день``, ``15 минут``."""
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "день" if days == 1 else count(days, "день", "дня", "дней")
    if minutes % 60 == 0:
        hours = minutes // 60
        return "час" if hours == 1 else count(hours, "час", "часа", "часов")
    return count(minutes, "минуту", "минуты", "минут")
