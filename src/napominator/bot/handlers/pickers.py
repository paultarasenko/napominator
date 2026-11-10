"""Calendar and time-of-day pickers (also used for snooze and for the daily anchors)."""

from __future__ import annotations

from datetime import date, datetime, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from napominator.application import users
from napominator.application.errors import InvalidScheduleError
from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, texts
from napominator.bot.deps import AppDeps
from napominator.bot.handlers.common import make_ctx
from napominator.bot.pickers import calendar_markup, hours_markup, minutes_markup
from napominator.bot.respond import Responder
from napominator.bot.states import Pick
from napominator.domain.entities import User

router = Router(name="pickers")

PICKED_DATE = "picked_date"
ANCHOR_PREFIX = "anchor_"


@router.callback_query(cb.Cal.filter(F.a == "none"))
async def inert(query: CallbackQuery) -> None:
    await query.answer()


@router.callback_query(cb.Cal.filter(F.a == "cancel"))
@router.callback_query(cb.Clk.filter(F.a == "cancel"))
async def cancelled(query: CallbackQuery, state: FSMContext) -> None:
    resp = Responder(query)
    await resp.ack()
    await state.clear()
    await resp.edit(texts.CANCELLED)


@router.callback_query(cb.Cal.filter(F.a == "nav"), Pick.date)
async def navigate(
    query: CallbackQuery,
    callback_data: cb.Cal,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    today = ctx.local_now.date()
    if (callback_data.y, callback_data.m) < (today.year, today.month):
        return
    await ctx.resp.edit(texts.PICK_DATE, calendar_markup(callback_data.y, callback_data.m, today))


@router.callback_query(cb.Cal.filter(F.a == "day"), Pick.date)
async def day_chosen(
    query: CallbackQuery,
    callback_data: cb.Cal,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    try:
        chosen = date(callback_data.y, callback_data.m, callback_data.d)
    except ValueError:
        await ctx.resp.send(texts.EXPIRED)
        return
    if chosen < ctx.local_now.date():
        await ctx.resp.send(texts.PAST)
        return
    await state.update_data({PICKED_DATE: chosen.isoformat()})
    await state.set_state(Pick.hour)
    await ctx.resp.edit(texts.PICK_HOUR, hours_markup())


@router.callback_query(cb.Clk.filter(F.a == "hour"), Pick.hour)
async def hour_chosen(query: CallbackQuery, callback_data: cb.Clk, state: FSMContext) -> None:
    resp = Responder(query)
    await resp.ack()
    await state.set_state(Pick.minute)
    await resp.edit(texts.PICK_MINUTE, minutes_markup(callback_data.h))


@router.callback_query(cb.Clk.filter(F.a == "back"), Pick.minute)
async def back_to_hours(query: CallbackQuery, state: FSMContext) -> None:
    resp = Responder(query)
    await resp.ack()
    await state.set_state(Pick.hour)
    await resp.edit(texts.PICK_HOUR, hours_markup())


@router.callback_query(cb.Clk.filter(F.a == "min"), Pick.minute)
async def minute_chosen(
    query: CallbackQuery,
    callback_data: cb.Clk,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    data = await state.get_data()
    purpose = str(data.get(flows.PURPOSE_KEY, ""))
    picked = time(callback_data.h, callback_data.m)
    if purpose.startswith(ANCHOR_PREFIX):
        await _save_anchor(ctx, purpose.removeprefix(ANCHOR_PREFIX), picked)
        return
    raw = data.get(PICKED_DATE)
    if not isinstance(raw, str):
        await ctx.resp.send(texts.EXPIRED)
        return
    await flows.apply_time(ctx, datetime.combine(date.fromisoformat(raw), picked))


async def _save_anchor(ctx: flows.Ctx, which: str, value: time) -> None:
    user = ctx.user
    times = {"morning": user.morning_time, "midday": user.midday_time, "evening": user.evening_time}
    if which not in times:
        await ctx.resp.send(texts.EXPIRED)
        return
    times[which] = value
    try:
        await users.set_anchors(ctx.uow, user.id, **times)
    except InvalidScheduleError:
        await ctx.resp.send(texts.ANCHOR_ORDER)
        return
    await ctx.state.clear()
    await ctx.resp.send(texts.ANCHOR_SAVED)
