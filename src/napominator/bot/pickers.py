"""Inline calendar and time picker (pure builders; handlers interpret the callbacks)."""

from __future__ import annotations

import calendar
from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from napominator.bot.callbacks import Cal, Clk
from napominator.bot.formatting import MONTHS_NOM, WEEKDAYS_SHORT

_HOURS_PER_ROW = 6
_MINUTE_STEPS = tuple(range(0, 60, 5))


def _button(text: str, data: CallbackData | str) -> InlineKeyboardButton:
    packed = data if isinstance(data, str) else data.pack()
    return InlineKeyboardButton(text=text, callback_data=packed)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def calendar_markup(year: int, month: int, today: date) -> InlineKeyboardMarkup:
    """A month grid. Past days are inert; you cannot navigate before the current month."""
    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, +1)
    can_go_back = (year, month) > (today.year, today.month)
    back = (
        _button("◀", Cal(a="nav", y=prev_y, m=prev_m))
        if can_go_back
        else _button(" ", Cal(a="none"))
    )
    rows: list[list[InlineKeyboardButton]] = [
        [
            back,
            _button(f"{MONTHS_NOM[month - 1]} {year}", Cal(a="none")),
            _button("▶", Cal(a="nav", y=next_y, m=next_m)),
        ],
        [_button(d, Cal(a="none")) for d in WEEKDAYS_SHORT],
    ]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row: list[InlineKeyboardButton] = []
        for day in week:
            if day.month != month or day < today:
                row.append(_button("·" if day.month == month else " ", Cal(a="none")))
            else:
                label = f"[{day.day}]" if day == today else str(day.day)
                row.append(_button(label, Cal(a="day", y=day.year, m=day.month, d=day.day)))
        rows.append(row)
    rows.append([_button("✖ Отмена", Cal(a="cancel"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hours_markup() -> InlineKeyboardMarkup:
    """Hours 07..23 first (what people usually mean), then the night hours."""
    order = [*range(7, 24), *range(0, 7)]
    rows = [
        [_button(f"{h:02d}", Clk(a="hour", h=h)) for h in order[i : i + _HOURS_PER_ROW]]
        for i in range(0, len(order), _HOURS_PER_ROW)
    ]
    rows.append([_button("✖ Отмена", Clk(a="cancel"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minutes_markup(hour: int) -> InlineKeyboardMarkup:
    steps = _MINUTE_STEPS
    rows = [
        [_button(f"{hour:02d}:{m:02d}", Clk(a="min", h=hour, m=m)) for m in steps[i : i + 4]]
        for i in range(0, len(steps), 4)
    ]
    rows.append([_button("◀ Часы", Clk(a="back")), _button("✖ Отмена", Clk(a="cancel"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)
