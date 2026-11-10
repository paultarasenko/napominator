from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from aiogram.types import InlineKeyboardMarkup

from napominator.bot import callbacks as cb
from napominator.bot import keyboards
from napominator.bot.formatting import (
    count,
    describe_delta,
    describe_moment,
    describe_rule,
    h,
    plural,
    short_moment,
)
from napominator.bot.pickers import calendar_markup, hours_markup, minutes_markup, shift_month
from napominator.domain.recurrence import Frequency, Recurrence

NOW = datetime(2026, 10, 13, 15, 0)


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "день"), (2, "дня"), (4, "дня"), (5, "дней"), (11, "дней"), (12, "дней"), (14, "дней"),
     (21, "день"), (22, "дня"), (25, "дней"), (101, "день"), (111, "дней"), (0, "дней")],
)  # fmt: skip
def test_plural(n: int, word: str) -> None:
    assert plural(n, "день", "дня", "дней") == word
    assert count(n, "день", "дня", "дней") == f"{n} {word}"


def test_html_is_escaped() -> None:
    assert h("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 13, 18, 0), "сегодня, 13 октября в 18:00"),
        (datetime(2026, 10, 14, 9, 5), "завтра, 14 октября в 09:05"),
        (datetime(2026, 10, 15, 9, 0), "послезавтра, 15 октября в 09:00"),
        (datetime(2026, 10, 16, 10, 30), "пт, 16 октября в 10:30"),
        (datetime(2027, 1, 5, 8, 0), "вт, 5 января 2027 в 08:00"),
    ],
)
def test_describe_moment(moment: datetime, expected: str) -> None:
    assert describe_moment(moment, NOW) == expected


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 13, 18, 0), "18:00"),
        (datetime(2026, 10, 14, 9, 0), "завтра 09:00"),
        (datetime(2026, 10, 16, 10, 30), "пт 16.10 10:30"),
    ],
)
def test_short_moment(moment: datetime, expected: str) -> None:
    assert short_moment(moment, NOW) == expected


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(minutes=1), "через 1 минуту"),
        (timedelta(minutes=20), "через 20 минут"),
        (timedelta(hours=2), "через 2 ч"),
        (timedelta(minutes=135), "через 2 ч 15 мин"),
        (timedelta(days=3), "через 3 дня"),
        (timedelta(seconds=-5), "через 0 минут"),
    ],
)
def test_describe_delta(delta: timedelta, expected: str) -> None:
    assert describe_delta(delta) == expected


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (None, ""),
        (Recurrence(Frequency.MINUTELY, 5), "каждые 5 минут"),
        (Recurrence(Frequency.MINUTELY, 30), "каждые 30 минут"),
        (Recurrence(Frequency.HOURLY), "каждый час"),
        (Recurrence(Frequency.HOURLY, 2), "каждые 2 часа"),
        (Recurrence(Frequency.DAILY), "каждый день"),
        (Recurrence(Frequency.DAILY, 3), "каждые 3 дня"),
        (Recurrence(Frequency.WEEKLY), "каждую неделю"),
        (Recurrence(Frequency.WEEKLY, 2), "каждые 2 недели"),
        (Recurrence(Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4)), "по будням"),
        (Recurrence(Frequency.WEEKLY, byweekday=(5, 6)), "по выходным"),
        (Recurrence(Frequency.WEEKLY, byweekday=(0,)), "по понедельникам"),
        (Recurrence(Frequency.WEEKLY, byweekday=(1, 3)), "по вторникам и четвергам"),
        (Recurrence(Frequency.WEEKLY, byweekday=(0, 2, 4)), "по понедельникам, средам и пятницам"),
        (Recurrence(Frequency.WEEKLY, 2, byweekday=(5,)), "каждые 2 недели, сб"),
        (Recurrence(Frequency.MONTHLY), "каждый месяц"),
        (Recurrence(Frequency.MONTHLY, 3), "каждые 3 месяца"),
        (Recurrence(Frequency.MONTHLY, bymonthday=(15,)), "каждый месяц, 15-го числа"),
        (Recurrence(Frequency.MONTHLY, bymonthday=(-1,)), "в последний день месяца"),
        (Recurrence(Frequency.YEARLY), "каждый год"),
        (Recurrence(Frequency.YEARLY, 2), "каждые 2 года"),
    ],
)
def test_describe_rule(rule: Recurrence | None, expected: str) -> None:
    assert describe_rule(rule) == expected


# --- pickers ---


def test_shift_month_crosses_year_boundaries() -> None:
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 10, 0) == (2026, 10)
    assert shift_month(2026, 3, 14) == (2027, 5)


def _texts(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _data(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def test_calendar_marks_today_and_blocks_the_past() -> None:
    markup = calendar_markup(2026, 10, date(2026, 10, 13))
    texts = _texts(markup)
    assert "Октябрь 2026" in texts
    assert "[13]" in texts
    assert "12" not in texts  # past days are replaced by inert dots
    assert "31" in texts
    day_buttons = [d for d in _data(markup) if d.startswith("ca:day")]
    assert len(day_buttons) == 19  # 13..31
    assert "◀" not in texts  # cannot go before the current month
    assert "▶" in texts
    assert all(len(d.encode()) <= 64 for d in _data(markup))


def test_calendar_in_a_later_month_allows_going_back_and_has_no_inert_days() -> None:
    markup = calendar_markup(2026, 11, date(2026, 10, 13))
    assert "◀" in _texts(markup)
    assert len([d for d in _data(markup) if d.startswith("ca:day")]) == 30
    assert "[13]" not in _texts(markup)


def test_hours_and_minutes() -> None:
    hours = _texts(hours_markup())
    assert hours[:3] == ["07", "08", "09"]  # waking hours first
    assert sorted(h for h in hours if h.isdigit()) == [f"{i:02d}" for i in range(24)]
    minutes = _texts(minutes_markup(18))
    assert minutes[0] == "18:00"
    assert "18:55" in minutes
    assert "◀ Часы" in minutes


# --- keyboards ---


def test_every_callback_fits_telegrams_64_byte_limit() -> None:
    samples = [
        cb.Nav(to="today"), cb.TzPick(a="zone", i=60), cb.Card(a="restore", r=2**31),
        cb.CardCat(r=2**31, c=2**31), cb.Ack(a="undo", n=2**31), cb.Snooze(n=2**31, c="tomorrow"),
        cb.Lst(f="recurring", p=999, c=2**31), cb.CatMenu(a="delmove", c=2**31, t=2**31),
        cb.Clarify(a="tmr_morning", v=1), cb.Cal(a="day", y=2026, m=12, d=31),
        cb.Clk(a="min", h=23, m=55), cb.Setting(a="evening", v=1),
    ]  # fmt: skip
    for sample in samples:
        assert len(sample.pack().encode()) <= 64, sample


def test_zone_picker_indexes_are_stable_and_bounded() -> None:
    assert keyboards.zone_by_flat_index(1) == "Europe/Moscow"
    assert keyboards.zone_by_flat_index(-1) is None
    assert keyboards.zone_by_flat_index(10_000) is None
    group = keyboards.timezone_zones(1)
    flat = [b.callback_data for row in group.inline_keyboard for b in row]
    zones = {
        keyboards.zone_by_flat_index(cb.TzPick.unpack(d).i)
        for d in flat
        if d and d.startswith("tz:zone")
    }
    assert "Europe/Kyiv" in zones
    assert "Europe/Moscow" not in zones
