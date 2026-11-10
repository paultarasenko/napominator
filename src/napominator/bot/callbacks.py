"""Typed callback data (Telegram limits it to 64 bytes: keep prefixes and fields short)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="nv"):
    to: str  # today | list | new | cats | set | help | menu


class TzPick(CallbackData, prefix="tz"):
    a: str  # yes | fix | city | list | grp | zone | keep | moved
    i: int = 0  # group or zone index into the curated list


class Card(CallbackData, prefix="cd"):
    a: str  # open | text | time | cat | del | restore | pause | resume | rule | back
    r: int  # reminder id


class CardCat(CallbackData, prefix="cc"):
    r: int
    c: int  # category id, 0 = none


class Ack(CallbackData, prefix="ak"):
    a: str  # done | snz | undo
    n: int  # notification id


class Snooze(CallbackData, prefix="sz"):
    n: int
    c: str  # 15m | 1h | 3h | eve | tmr | own


class Lst(CallbackData, prefix="ls"):
    f: str  # soon | recurring | done | all | cat
    p: int = 0  # page
    c: int = 0  # category id (for f == "cat")


class CatMenu(CallbackData, prefix="ct"):
    a: str  # open | new | rename | emoji | up | down | del | delmove | delgo | list
    c: int = 0
    t: int = 0  # target category for delete/move (0 = none)


class Clarify(CallbackData, prefix="cl"):
    a: str  # in1h | today_eve | tmr_morning | tmr_eve | pick | ok | edit | cancel | ...
    v: int = 0


class Cal(CallbackData, prefix="ca"):
    a: str  # day | nav | none | cancel
    y: int = 0
    m: int = 0
    d: int = 0


class Clk(CallbackData, prefix="tm"):
    a: str  # hour | min | cancel
    h: int = 0
    m: int = 0


class Setting(CallbackData, prefix="st"):
    a: str  # tz | morning | midday | evening | back
    v: int = 0


class Privacy(CallbackData, prefix="pv"):
    a: str  # export | delete | delete_yes | delete_no
