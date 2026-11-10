"""Creating a reminder: free text, /new, clarification chips, text/time replies."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.handlers.common import make_ctx, require_onboarding
from napominator.bot.pending import Pending
from napominator.bot.states import Create, New
from napominator.domain.entities import User
from napominator.nlp import Flag

router = Router(name="create")


@router.message(Command("new"))
@router.message(F.text == keyboards.BTN_NEW)
async def new_command(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if not await require_onboarding(ctx):
        return
    await state.clear()
    await state.set_state(New.text)
    await message.answer(texts.NEW_ASK_TEXT)


@router.callback_query(cb.Nav.filter(F.to == "new"))
async def new_button(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    await state.set_state(New.text)
    await ctx.resp.send(texts.NEW_ASK_TEXT)


@router.message(Create.clarify, F.text)
@router.message(Create.confirm, F.text)
async def time_reply(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    """The bot asked "when?" (or "is this right?") and the user typed something."""
    ctx = make_ctx(message, state, deps, uow, user)
    result = await flows.parse_text(ctx, message.text or "")
    if result.text and result.when is not None and Flag.PAST_DATE not in result.flags:
        await flows.advance(ctx, Pending.from_result(result))  # a whole new reminder, not a reply
        return
    usable = flows.typed_time(result)
    if usable is None:
        reply = texts.PAST if Flag.PAST_DATE in result.flags else texts.NOT_UNDERSTOOD_TIME
        await message.answer(reply, reply_markup=keyboards.when_chips())
        return
    await flows.apply_time(ctx, *usable)


@router.message(Create.text, F.text)
async def body_reply(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    data = await state.get_data()
    pending = Pending.from_dict(data[flows.PENDING_KEY])
    pending.text = (message.text or "").strip()
    pending.confident = not pending.has(Flag.TIME_UNCERTAIN) and not pending.has(
        Flag.MULTIPLE_TIMES
    )
    await flows.advance(ctx, pending)


@router.message(StateFilter(None, New.text), F.forward_origin)
async def forwarded(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    """A forwarded message becomes the reminder text; we only need to ask *when* (spec S2)."""
    ctx = make_ctx(message, state, deps, uow, user)
    if not await require_onboarding(ctx):
        return
    body = (message.text or message.caption or "").strip()
    if not body:
        await message.answer(texts.FORWARD_EMPTY)
        return
    await state.clear()
    await flows.advance(ctx, Pending(text=body[:1000]))


@router.message(StateFilter(None, New.text), F.text, ~F.text.startswith("/"))
async def free_text(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if not await require_onboarding(ctx):
        return
    await state.clear()
    await flows.begin(ctx, message.text or "")


@router.callback_query(cb.Clarify.filter())
async def clarify_button(
    query: CallbackQuery,
    callback_data: cb.Clarify,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    action = callback_data.a
    if action == "cancel":
        await state.clear()
        await ctx.resp.edit(texts.CANCELLED)
        return
    data = await state.get_data()
    purpose = data.get(flows.PURPOSE_KEY)
    if purpose is None:
        await ctx.resp.send(texts.EXPIRED)
        return
    if action == "ok" and flows.PENDING_KEY in data:
        pending = Pending.from_dict(data[flows.PENDING_KEY])
        pending.confident = True
        await flows.create_now(ctx, pending)
    elif action == "edit":
        await state.set_state(Create.clarify)
        await ctx.resp.send(texts.ASK_WHEN, keyboards.when_chips())
    elif action == "pick":
        await flows.start_picker(ctx, purpose, data.get(flows.TARGET_KEY, 0))
    else:
        when = flows.chip_time(action, user, ctx.now)
        if when is None:
            await ctx.resp.send(texts.EXPIRED)
            return
        await flows.apply_time(ctx, when)


# --- fallbacks (registered last) ---


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer("Не знаю такой команды. Напиши /help — там всё, что я умею.")


@router.message()
async def not_text(message: Message) -> None:
    await message.answer("Я понимаю только текст. Напиши, о чём и когда напомнить.")
