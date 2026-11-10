"""Buttons under a delivered notification: done, snooze, undo."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from napominator.application import actions
from napominator.application.errors import (
    ActionExpiredError,
    InvalidScheduleError,
    NotFoundError,
)
from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.formatting import h
from napominator.bot.handlers.common import make_ctx
from napominator.bot.respond import Responder
from napominator.domain.entities import User
from napominator.domain.timeutil import local_to_utc, utc_to_local

router = Router(name="ack")


async def _reminder_text(ctx: flows.Ctx, notification_id: int) -> str:
    note = await ctx.uow.notifications.get_for_user(notification_id, ctx.user.id)
    if note is None or note.reminder_id is None:
        return ""
    reminder = await ctx.uow.reminders.get(note.reminder_id, ctx.user.id)
    return h(reminder.text) if reminder else ""


@router.callback_query(cb.Ack.filter(F.a == "done"))
async def done(
    query: CallbackQuery,
    callback_data: cb.Ack,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    try:
        await actions.mark_done(uow, user.id, callback_data.n, ctx.now)
    except NotFoundError:
        await ctx.resp.send(texts.GONE)
        return
    except ActionExpiredError:
        await ctx.resp.edit_markup(None)
        return
    body = await _reminder_text(ctx, callback_data.n)
    await ctx.resp.edit(texts.DONE.format(text=body), keyboards.undo(callback_data.n))


@router.callback_query(cb.Ack.filter(F.a == "snz"))
async def snooze_menu(query: CallbackQuery, callback_data: cb.Ack) -> None:
    resp = Responder(query)
    await resp.ack()
    await resp.edit_markup(keyboards.snooze_menu(callback_data.n))


@router.callback_query(cb.Ack.filter(F.a == "back"))
async def back(query: CallbackQuery, callback_data: cb.Ack) -> None:
    resp = Responder(query)
    await resp.ack()
    await resp.edit_markup(keyboards.ack(callback_data.n))


@router.callback_query(cb.Ack.filter(F.a == "undo"))
async def undo(
    query: CallbackQuery,
    callback_data: cb.Ack,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    try:
        await actions.undo_reaction(uow, user.id, callback_data.n, ctx.now)
    except (ActionExpiredError, NotFoundError):
        await ctx.resp.edit_markup(None)
        return
    body = await _reminder_text(ctx, callback_data.n)
    await ctx.resp.edit(f"⏰ <b>{body}</b>", keyboards.ack(callback_data.n))


@router.callback_query(cb.Snooze.filter())
async def snooze_choice(
    query: CallbackQuery,
    callback_data: cb.Snooze,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    choice = callback_data.c
    if choice == "own":
        await state.clear()
        await flows.start_picker(ctx, "snooze", callback_data.n)
        return
    try:
        until = _target(ctx, choice)
        await actions.snooze(uow, user.id, callback_data.n, until, ctx.now)
    except (InvalidScheduleError, ActionExpiredError, NotFoundError):
        await ctx.resp.edit_markup(None)
        return
    when = ctx.describe(utc_to_local(until, ctx.zone))
    body = await _reminder_text(ctx, callback_data.n)
    await ctx.resp.edit(texts.SNOOZED.format(when=when, text=body), keyboards.undo(callback_data.n))


def _target(ctx: flows.Ctx, choice: str):  # type: ignore[no-untyped-def]
    user = ctx.user
    if choice in {"eve", "tmr"}:
        chip = "today_eve" if choice == "eve" else "tmr_morning"
        local = flows.chip_time(chip, user, ctx.now)
        if local is None:
            raise InvalidScheduleError(choice)
        return local_to_utc(local, ctx.zone)
    return actions.snooze_target(ctx.now, user.timezone or "UTC", choice=choice)
