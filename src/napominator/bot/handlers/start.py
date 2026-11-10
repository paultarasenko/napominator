"""/start, onboarding, time-zone selection, /help, /cancel."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application import actions, users
from napominator.application.errors import InvalidScheduleError
from napominator.application.ports import UnitOfWork
from napominator.application.reminders import create_reminder
from napominator.application.users import Registration
from napominator.bot import callbacks as cb
from napominator.bot import keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.handlers.common import make_ctx
from napominator.bot.respond import Responder
from napominator.bot.states import Onboarding
from napominator.domain.entities import NewReminder, User
from napominator.domain.timeutil import is_valid_timezone, load_zone, utc_to_local
from napominator.domain.timezones import zone_label

router = Router(name="start")

FLOW = "flow"  # "onboarding" | "settings"
CANDIDATE = "tz_candidate"


@router.message(CommandStart())
async def start(
    message: Message, state: FSMContext, user: User, registration: Registration
) -> None:
    await state.clear()
    if user.timezone is None:
        await state.update_data({FLOW: "onboarding"})
        await message.answer(texts.WELCOME, reply_markup=keyboards.timezone_methods())
        return
    note = texts.WELCOME_BACK
    if registration.resumed_series:
        note += "\n" + texts.SERIES_RESUMED.format(n=registration.resumed_series)
    await message.answer(note, reply_markup=keyboards.main_menu())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(texts.HELP, reply_markup=keyboards.main_menu())


@router.callback_query(cb.Nav.filter(F.to == "help"))
async def help_button(query: CallbackQuery) -> None:
    await Responder(query).ack()
    await Responder(query).send(texts.HELP)


@router.callback_query(cb.Nav.filter(F.to == "menu"))
async def menu_button(query: CallbackQuery) -> None:
    await Responder(query).ack()
    await Responder(query).send(texts.WELCOME_BACK, keyboards.main_menu())


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL, reply_markup=keyboards.main_menu())
        return
    await state.clear()
    await message.answer(texts.CANCELLED, reply_markup=keyboards.main_menu())


# --- choosing how to detect the zone ---


@router.message(F.text == keyboards.BTN_CITY)
async def ask_city(message: Message, state: FSMContext) -> None:
    await state.set_state(Onboarding.city)
    await message.answer(texts.TZ_ASK_CITY)


@router.message(F.text == keyboards.BTN_ZONE_LIST)
async def pick_from_list(message: Message) -> None:
    await message.answer(texts.TZ_PICK_REGION, reply_markup=keyboards.timezone_groups())


@router.message(Onboarding.city, F.text)
async def city_typed(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    match = await asyncio.to_thread(deps.tz.from_city, message.text or "")
    if match is None:
        await message.answer(texts.TZ_CITY_NOT_FOUND, reply_markup=keyboards.timezone_methods())
        return
    await offer(make_ctx(message, state, deps, uow, user), match.timezone)


@router.message(F.location)
async def location_shared(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    assert message.location is not None  # noqa: S101 - guaranteed by the filter
    zone = await asyncio.to_thread(
        deps.tz.from_location, message.location.latitude, message.location.longitude
    )
    if zone is None:
        await message.answer(texts.TZ_LOCATION_FAILED, reply_markup=keyboards.timezone_methods())
        return
    await offer(make_ctx(message, state, deps, uow, user), zone)


async def offer(ctx, zone: str) -> None:  # type: ignore[no-untyped-def]
    """Show "it is HH:MM for you, right?" for a candidate zone."""
    local = utc_to_local(ctx.now, load_zone(zone))
    await ctx.state.update_data({CANDIDATE: zone})
    await ctx.state.set_state(None)
    await ctx.resp.send(texts.tz_confirm(local, zone_label(zone)), keyboards.timezone_confirm())


@router.callback_query(cb.TzPick.filter(F.a == "fix"))
async def fix(query: CallbackQuery) -> None:
    await Responder(query).ack()
    await Responder(query).send(texts.TZ_PICK_METHOD, keyboards.timezone_methods())


@router.callback_query(cb.TzPick.filter(F.a == "list"))
async def groups(query: CallbackQuery) -> None:
    resp = Responder(query)
    await resp.ack()
    await resp.edit(texts.TZ_PICK_REGION, keyboards.timezone_groups())


@router.callback_query(cb.TzPick.filter(F.a == "grp"))
async def zones(query: CallbackQuery, callback_data: cb.TzPick) -> None:
    resp = Responder(query)
    await resp.ack()
    await resp.edit(texts.TZ_PICK_ZONE, keyboards.timezone_zones(callback_data.i))


@router.callback_query(cb.TzPick.filter(F.a == "zone"))
async def zone_chosen(
    query: CallbackQuery,
    callback_data: cb.TzPick,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    zone = keyboards.zone_by_flat_index(callback_data.i)
    if zone is None:
        await ctx.resp.send(texts.EXPIRED)
        return
    await offer(ctx, zone)


# --- confirming ---


@router.callback_query(cb.TzPick.filter(F.a == "yes"), StateFilter(None))
async def confirmed(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    data = await state.get_data()
    zone = data.get(CANDIDATE)
    if not isinstance(zone, str) or not is_valid_timezone(zone):
        await ctx.resp.send(texts.EXPIRED)
        return
    if user.timezone is None:
        await finish_onboarding(ctx, zone)
    elif zone == user.timezone:
        await state.clear()
        await ctx.resp.send(texts.onboarding_done(zone_label(zone)), keyboards.main_menu())
    elif await uow.reminders.count_active(user.id):
        await ctx.resp.send(texts.TZ_CHANGE_MODE, keyboards.timezone_change_mode())
    else:
        await apply_change(ctx, zone, keep_local_time=True)


async def finish_onboarding(ctx, zone: str) -> None:  # type: ignore[no-untyped-def]
    try:
        await users.complete_onboarding(ctx.uow, ctx.user.id, zone, ctx.now)
    except InvalidScheduleError:
        await ctx.resp.send(texts.EXPIRED)
        return
    await ctx.state.clear()
    await ctx.resp.send(texts.onboarding_done(zone_label(zone)), keyboards.main_menu())
    await ctx.resp.send(texts.DEMO_OFFER, keyboards.demo())


@router.callback_query(cb.TzPick.filter(F.a.in_({"keep", "moved"})))
async def change_mode(
    query: CallbackQuery,
    callback_data: cb.TzPick,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    zone = (await state.get_data()).get(CANDIDATE)
    if not isinstance(zone, str):
        await ctx.resp.send(texts.EXPIRED)
        return
    await apply_change(ctx, zone, keep_local_time=callback_data.a == "keep")


async def apply_change(ctx, zone: str, *, keep_local_time: bool) -> None:  # type: ignore[no-untyped-def]
    replanned = await actions.change_timezone(
        ctx.uow, ctx.user, zone, keep_local_time=keep_local_time, now=ctx.now
    )
    await ctx.state.clear()
    await ctx.resp.send(
        texts.TZ_CHANGED.format(zone=zone_label(zone), n=replanned), keyboards.main_menu()
    )


@router.callback_query(cb.Nav.filter(F.to == "demo"))
async def demo(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    """Spec S9: a real reminder that fires in about a minute, so the user sees the whole loop."""
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    if user.timezone is None:
        await ctx.resp.send(texts.TZ_NEED_FIRST, keyboards.timezone_methods())
        return
    when = ctx.local_now.replace(second=0, microsecond=0) + timedelta(minutes=2)
    created = await create_reminder(
        uow, NewReminder(user.id, texts.DEMO_TEXT, user.timezone, when), ctx.now
    )
    await ctx.resp.send(
        texts.DEMO_STARTED.format(
            when=ctx.describe(utc_to_local(created.first_occurrence, ctx.zone))
        )
    )
