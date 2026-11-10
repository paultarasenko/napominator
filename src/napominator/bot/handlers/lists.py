"""/today and the reminder lists."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application import queries, search
from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.handlers.common import make_ctx, require_onboarding
from napominator.bot.states import Search
from napominator.domain.entities import ReminderFilter, ReminderStatus, User

router = Router(name="lists")


async def show_today(ctx: flows.Ctx) -> None:
    view = await queries.today(ctx.uow, ctx.user, ctx.now)
    tz = ctx.user.timezone or "UTC"
    await ctx.resp.send(
        texts.today_screen(view, tz, ctx.now),
        keyboards.today(view.upcoming, view.awaiting, tz, ctx.now),
    )


@router.message(Command("today"))
@router.message(F.text == keyboards.BTN_TODAY)
async def today_command(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if await require_onboarding(ctx):
        await state.clear()
        await show_today(ctx)


@router.callback_query(cb.Nav.filter(F.to == "today"))
async def today_button(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await show_today(ctx)


async def show_list(ctx: flows.Ctx, active: str, page: int, category_id: int) -> None:
    if active == "cat":
        flt = ReminderFilter(
            statuses=(ReminderStatus.ACTIVE, ReminderStatus.PAUSED), category_id=category_id
        )
    else:
        flt = queries.LIST_FILTERS.get(active, queries.LIST_FILTERS["soon"])
    result = await queries.page_of(ctx.uow, ctx.user.id, flt, page)
    cats = await ctx.uow.categories.list(ctx.user.id)
    category = next((c for c in cats if c.id == category_id), None)
    await ctx.resp.edit(
        texts.list_screen(active, result, category),
        keyboards.reminder_list(
            result,
            active=active,
            category_id=category_id,
            categories=cats,
            tz_name=ctx.user.timezone or "UTC",
            now=ctx.now,
        ),
    )


@router.message(Command("list"))
@router.message(F.text == keyboards.BTN_ALL)
async def list_command(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if await require_onboarding(ctx):
        await state.clear()
        await show_list(ctx, "soon", 0, 0)


@router.callback_query(cb.Nav.filter(F.to == "list"))
@router.callback_query(cb.Lst.filter())
async def list_button(
    query: CallbackQuery,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
    callback_data: cb.Lst | None = None,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    if isinstance(callback_data, cb.Lst):
        await show_list(ctx, callback_data.f, callback_data.p, callback_data.c)
    else:
        await show_list(ctx, "soon", 0, 0)


# --- search (S8) ---


async def show_found(ctx: flows.Ctx, query: str) -> None:
    pool = await ctx.uow.reminders.search(
        ctx.user.id,
        ReminderFilter(
            statuses=(ReminderStatus.ACTIVE, ReminderStatus.PAUSED, ReminderStatus.DONE),
            order="created",
        ),
        limit=1000,
    )
    found = search.find([v.reminder for v in pool], query)
    if not found:
        await ctx.resp.send(texts.SEARCH_EMPTY)
        return
    await ctx.resp.send(texts.SEARCH_FOUND.format(n=len(found)), keyboards.search_results(found))


@router.message(Command("search"))
async def search_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if not await require_onboarding(ctx):
        return
    await state.clear()
    if command.args:
        await show_found(ctx, command.args)
        return
    await state.set_state(Search.query)
    await message.answer(texts.SEARCH_ASK)


@router.callback_query(cb.Nav.filter(F.to == "search"))
async def search_button(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.set_state(Search.query)
    await ctx.resp.send(texts.SEARCH_ASK)


@router.message(Search.query, F.text, ~F.text.startswith("/"))
async def search_text(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    await state.clear()
    await show_found(ctx, message.text or "")
