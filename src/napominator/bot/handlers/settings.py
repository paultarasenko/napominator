"""/settings: time zone and the times of "morning / midday / evening"."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.formatting import clock
from napominator.bot.handlers.common import make_ctx, require_onboarding
from napominator.bot.handlers.pickers import ANCHOR_PREFIX
from napominator.bot.pickers import hours_markup
from napominator.bot.states import Pick
from napominator.domain.entities import User
from napominator.domain.timezones import zone_label

router = Router(name="settings")
_NAMES = {"morning": "утро", "midday": "день", "evening": "вечер"}


async def show_settings(ctx: flows.Ctx) -> None:
    user = ctx.user
    text = texts.settings_screen(
        zone_label(user.timezone or "UTC"),
        clock(user.morning_time),
        clock(user.midday_time),
        clock(user.evening_time),
    )
    await ctx.resp.send(text, keyboards.settings())


@router.message(Command("settings"))
@router.message(F.text == keyboards.BTN_SETTINGS)
async def settings_command(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if await require_onboarding(ctx):
        await state.clear()
        await show_settings(ctx)


@router.callback_query(cb.Nav.filter(F.to == "set"))
async def settings_button(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await show_settings(ctx)


@router.callback_query(cb.Setting.filter(F.a == "tz"))
async def change_timezone(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    await state.update_data(flow="settings")
    await ctx.resp.send(texts.TZ_PICK_METHOD, keyboards.timezone_methods())


@router.callback_query(cb.Setting.filter(F.a.in_(set(_NAMES))))
async def change_anchor(
    query: CallbackQuery,
    callback_data: cb.Setting,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    await state.update_data({flows.PURPOSE_KEY: ANCHOR_PREFIX + callback_data.a})
    await state.set_state(Pick.hour)
    await ctx.resp.send(texts.ASK_ANCHOR.format(name=_NAMES[callback_data.a]), hours_markup())
