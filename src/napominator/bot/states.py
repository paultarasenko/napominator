"""FSM states. Everything a flow needs between messages lives in the FSM data dict."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    city = State()  # waiting for a city name (also used by Settings -> time zone)


class Create(StatesGroup):
    clarify = State()  # waiting for the time (typed or chosen with buttons)
    text = State()  # the time is known, waiting for the body
    confirm = State()  # shown an interpretation, waiting for "верно"/"изменить"


class New(StatesGroup):
    text = State()  # /new: waiting for the whole phrase


class Pick(StatesGroup):
    date = State()  # inline calendar is on screen
    hour = State()  # hours grid is on screen
    minute = State()  # minutes grid is on screen


class Edit(StatesGroup):
    text = State()  # waiting for a new body
    time = State()  # waiting for a new time phrase


class Cat(StatesGroup):
    new = State()
    rename = State()
    emoji = State()


class Search(StatesGroup):
    query = State()
