"""Keyboards. Handlers never build buttons inline: everything user-visible lives here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from napominator.application.queries import Page
from napominator.bot import callbacks as cb
from napominator.bot.formatting import short_moment
from napominator.domain.entities import (
    AwaitingAck,
    Category,
    Reminder,
    ReminderStatus,
    ReminderView,
)
from napominator.domain.timeutil import load_zone, utc_to_local
from napominator.domain.timezones import POPULAR_ZONES

BTN_NEW = "➕ Новое"
BTN_TODAY = "📅 Сегодня"
BTN_ALL = "📋 Все"
BTN_CATEGORIES = "🗂 Категории"
BTN_SETTINGS = "⚙️ Настройки"
MENU_BUTTONS = frozenset({BTN_NEW, BTN_TODAY, BTN_ALL, BTN_CATEGORIES, BTN_SETTINGS})

BTN_LOCATION = "📍 Отправить геопозицию"
BTN_CITY = "🏙 Ввести город"
BTN_ZONE_LIST = "🗺 Выбрать из списка"

_ROW = 2


def _b(text: str, data: CallbackData | str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=data if isinstance(data, str) else data.pack()
    )


def _rows(
    buttons: Sequence[InlineKeyboardButton], per_row: int = _ROW
) -> list[list[InlineKeyboardButton]]:
    return [list(buttons[i : i + per_row]) for i in range(0, len(buttons), per_row)]


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- reply keyboards ---


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_TODAY)],
            [KeyboardButton(text=BTN_ALL), KeyboardButton(text=BTN_CATEGORIES)],
            [KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def timezone_methods() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LOCATION, request_location=True)],
            [KeyboardButton(text=BTN_CITY), KeyboardButton(text=BTN_ZONE_LIST)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# --- onboarding ---


def timezone_confirm() -> InlineKeyboardMarkup:
    return _kb([[_b("✅ Да", cb.TzPick(a="yes")), _b("✏️ Нет, исправить", cb.TzPick(a="fix"))]])


def timezone_groups() -> InlineKeyboardMarkup:
    buttons = [_b(name, cb.TzPick(a="grp", i=i)) for i, (name, _) in enumerate(POPULAR_ZONES)]
    return _kb(_rows(buttons, 3))


def timezone_zones(group: int) -> InlineKeyboardMarkup:
    flat_index = sum(len(zones) for _, zones in POPULAR_ZONES[:group])
    _, zones = POPULAR_ZONES[group]
    buttons = [
        _b(label, cb.TzPick(a="zone", i=flat_index + n)) for n, (label, _) in enumerate(zones)
    ]
    rows = _rows(buttons, 3)
    rows.append([_b("◀ Назад", cb.TzPick(a="list"))])
    return _kb(rows)


def zone_by_flat_index(index: int) -> str | None:
    flat = [name for _, zones in POPULAR_ZONES for _, name in zones]
    return flat[index] if 0 <= index < len(flat) else None


def timezone_change_mode() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_b("🕘 Время на часах то же", cb.TzPick(a="keep"))],
            [_b("🌍 Тот же момент, другой пояс", cb.TzPick(a="moved"))],
        ]
    )


# --- creating a reminder ---


def when_chips(*, with_pick: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            _b("⏱ Через час", cb.Clarify(a="in1h")),
            _b("🌆 Сегодня вечером", cb.Clarify(a="today_eve")),
        ],
        [
            _b("🌅 Завтра утром", cb.Clarify(a="tmr_morning")),
            _b("🌃 Завтра вечером", cb.Clarify(a="tmr_eve")),
        ],
    ]
    if with_pick:
        rows.append([_b("📅 Выбрать дату и время", cb.Clarify(a="pick"))])
    rows.append([_b("✖ Отмена", cb.Clarify(a="cancel"))])
    return _kb(rows)


def confirm_guess() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_b("✅ Верно", cb.Clarify(a="ok")), _b("✏️ Изменить время", cb.Clarify(a="edit"))],
            [_b("✖ Отмена", cb.Clarify(a="cancel"))],
        ]
    )


def card(reminder: Reminder) -> InlineKeyboardMarkup:
    rid = reminder.id
    if reminder.status is ReminderStatus.DELETED:
        return _kb([[_b("↩️ Вернуть", cb.Card(a="restore", r=rid))]])
    rows = [
        [_b("⏰ Время", cb.Card(a="time", r=rid)), _b("✏️ Текст", cb.Card(a="text", r=rid))],
        [_b("🗂 Категория", cb.Card(a="cat", r=rid)), _b("🗑 Удалить", cb.Card(a="del", r=rid))],
    ]
    if reminder.is_recurring:
        pause = (
            _b("▶️ Возобновить", cb.Card(a="resume", r=rid))
            if reminder.status is ReminderStatus.PAUSED
            else _b("⏸ Пауза", cb.Card(a="pause", r=rid))
        )
        rows.append([pause])
    return _kb(rows)


def category_picker(
    categories: Sequence[Category], reminder_id: int, current: int | None
) -> InlineKeyboardMarkup:
    buttons = [
        _b(
            ("✔ " if c.id == current else "") + f"{c.emoji or '🗂'} {c.name}",
            cb.CardCat(r=reminder_id, c=c.id),
        )
        for c in categories
    ]
    rows = _rows(buttons)
    rows.append([_b("▫️ Без категории", cb.CardCat(r=reminder_id, c=0))])
    rows.append([_b("◀ Назад", cb.Card(a="back", r=reminder_id))])
    return _kb(rows)


def after_delete(reminder_id: int) -> InlineKeyboardMarkup:
    return _kb([[_b("↩️ Вернуть", cb.Card(a="restore", r=reminder_id))]])


# --- delivered notification ---


def ack(notification_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                _b("✅ Готово", cb.Ack(a="done", n=notification_id)),
                _b("⏰ Отложить", cb.Ack(a="snz", n=notification_id)),
            ]
        ]
    )


def snooze_menu(notification_id: int) -> InlineKeyboardMarkup:
    n = notification_id
    return _kb(
        [
            [
                _b("15 мин", cb.Snooze(n=n, c="15m")),
                _b("1 час", cb.Snooze(n=n, c="1h")),
                _b("3 часа", cb.Snooze(n=n, c="3h")),
            ],
            [_b("🌆 Вечером", cb.Snooze(n=n, c="eve")), _b("🌅 Завтра", cb.Snooze(n=n, c="tmr"))],
            [_b("📅 Своё время", cb.Snooze(n=n, c="own"))],
            [_b("◀ Назад", cb.Ack(a="back", n=n))],
        ]
    )


def undo(notification_id: int) -> InlineKeyboardMarkup:
    return _kb([[_b("↩️ Отменить", cb.Ack(a="undo", n=notification_id))]])


# --- lists ---


def today(
    upcoming: Sequence[ReminderView], awaiting: Sequence[AwaitingAck], tz_name: str, now: datetime
) -> InlineKeyboardMarkup:
    tz = load_zone(tz_name)
    now_local = utc_to_local(now, tz)
    rows: list[list[InlineKeyboardButton]] = []
    for item in awaiting[:5]:
        rows.append(
            [_b(f"✅ {_cut(item.reminder.text)}", cb.Ack(a="done", n=item.notification.id))]
        )
    for view in upcoming[:8]:
        when = short_moment(utc_to_local(view.next_at, tz), now_local) if view.next_at else "—"
        rows.append(
            [_b(f"{when} · {_cut(view.reminder.text)}", cb.Card(a="open", r=view.reminder.id))]
        )
    rows.append([_b("📋 Все напоминания", cb.Lst(f="soon")), _b("➕ Новое", cb.Nav(to="new"))])
    return _kb(rows)


def list_tabs(
    active: str, categories: Sequence[Category], category_id: int
) -> list[list[InlineKeyboardButton]]:
    tabs = [("soon", "Скоро"), ("recurring", "🔁 Повторы"), ("done", "Выполнено"), ("all", "Все")]
    rows = [[_b(("• " if key == active else "") + label, cb.Lst(f=key)) for key, label in tabs]]
    rows.append([_b("🔎 Поиск", cb.Nav(to="search"))])
    if categories:
        chips = [
            _b(
                ("• " if active == "cat" and category_id == c.id else "")
                + f"{c.emoji or '🗂'} {c.name}",
                cb.Lst(f="cat", c=c.id),
            )
            for c in categories
        ]
        rows.extend(_rows(chips, 3))
    return rows


def reminder_list(
    page: Page,
    *,
    active: str,
    category_id: int,
    categories: Sequence[Category],
    tz_name: str,
    now: datetime,
) -> InlineKeyboardMarkup:
    tz = load_zone(tz_name)
    now_local = utc_to_local(now, tz)
    rows = list_tabs(active, categories, category_id)
    for view in page.items:
        when = short_moment(utc_to_local(view.next_at, tz), now_local) if view.next_at else "—"
        rows.append(
            [_b(f"{when} · {_cut(view.reminder.text)}", cb.Card(a="open", r=view.reminder.id))]
        )
    if page.pages > 1:
        nav = [
            _b(
                "◀" if page.page > 0 else " ",
                cb.Lst(f=active, p=max(page.page - 1, 0), c=category_id),
            ),
            _b(f"{page.page + 1}/{page.pages}", cb.Lst(f=active, p=page.page, c=category_id)),
            _b(
                "▶" if page.page + 1 < page.pages else " ",
                cb.Lst(f=active, p=min(page.page + 1, page.pages - 1), c=category_id),
            ),
        ]
        rows.append(nav)
    return _kb(rows)


def _cut(text: str, limit: int = 28) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def search_results(found: Sequence[Reminder]) -> InlineKeyboardMarkup:
    rows = [[_b(_cut(r.text, 40), cb.Card(a="open", r=r.id))] for r in found]
    rows.append([_b("📋 Все напоминания", cb.Lst(f="soon"))])
    return _kb(rows)


# --- categories ---


def categories_screen(
    categories: Sequence[Category], counts: dict[int, int]
) -> InlineKeyboardMarkup:
    buttons = [
        _b(f"{c.emoji or '🗂'} {c.name} · {counts.get(c.id, 0)}", cb.CatMenu(a="open", c=c.id))
        for c in categories
    ]
    rows = _rows(buttons)
    rows.append([_b("➕ Новая категория", cb.CatMenu(a="new"))])
    return _kb(rows)


def category_menu(category: Category) -> InlineKeyboardMarkup:
    c = category.id
    return _kb(
        [
            [_b("📋 Напоминания", cb.Lst(f="cat", c=c))],
            [
                _b("✏️ Переименовать", cb.CatMenu(a="rename", c=c)),
                _b("🎨 Эмодзи", cb.CatMenu(a="emoji", c=c)),
            ],
            [
                _b("⬆️", cb.CatMenu(a="up", c=c)),
                _b("⬇️", cb.CatMenu(a="down", c=c)),
                _b("🗑 Удалить", cb.CatMenu(a="del", c=c)),
            ],
            [_b("◀ К категориям", cb.Nav(to="cats"))],
        ]
    )


def delete_category_targets(
    category: Category, others: Sequence[Category], reminders: int
) -> InlineKeyboardMarkup:
    if reminders == 0:
        return _kb(
            [
                [
                    _b("🗑 Удалить", cb.CatMenu(a="delgo", c=category.id)),
                    _b("◀ Нет", cb.CatMenu(a="open", c=category.id)),
                ]
            ]
        )
    buttons = [
        _b(f"{o.emoji or '🗂'} {o.name}", cb.CatMenu(a="delmove", c=category.id, t=o.id))
        for o in others
    ]
    rows = _rows(buttons)
    rows.append([_b("▫️ Оставить без категории", cb.CatMenu(a="delmove", c=category.id, t=0))])
    rows.append([_b("◀ Отмена", cb.CatMenu(a="open", c=category.id))])
    return _kb(rows)


# --- privacy ---


def privacy_actions() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_b("📤 Выгрузить мои данные", cb.Privacy(a="export"))],
            [_b("🗑 Удалить все мои данные", cb.Privacy(a="delete"))],
        ]
    )


def delete_account_confirm() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_b("🗑 Да, удалить навсегда", cb.Privacy(a="delete_yes"))],
            [_b("◀ Нет, оставить", cb.Privacy(a="delete_no"))],
        ]
    )


def demo() -> InlineKeyboardMarkup:
    return _kb([[_b("🎯 Попробовать на примере", cb.Nav(to="demo"))]])


# --- settings ---


def settings() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_b("🌍 Часовой пояс", cb.Setting(a="tz"))],
            [
                _b("🌅 Утро", cb.Setting(a="morning")),
                _b("☀️ День", cb.Setting(a="midday")),
                _b("🌆 Вечер", cb.Setting(a="evening")),
            ],
            [_b("🔒 Мои данные", cb.Nav(to="privacy")), _b("ℹ️ Помощь", cb.Nav(to="help"))],
        ]
    )
