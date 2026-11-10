"""A reminder's card and the actions on it."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application import actions, categories
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
from napominator.bot.states import Edit
from napominator.domain.entities import (
    OPEN_STATUSES,
    NotificationKind,
    Reminder,
    ReminderStatus,
    User,
)
from napominator.domain.timeutil import utc_to_local
from napominator.nlp import Flag

router = Router(name="cards")


async def render_card(ctx: flows.Ctx, reminder: Reminder, header: str = "📌 Напоминание") -> None:
    """Edit (or send) the card of ``reminder`` with the right buttons."""
    notes = await ctx.uow.notifications.list_for_reminder(reminder.id)
    upcoming = [
        n.fire_at
        for n in notes
        if n.status in OPEN_STATUSES and n.kind in (NotificationKind.DUE, NotificationKind.SNOOZE)
    ]
    when = utc_to_local(min(upcoming), ctx.zone) if upcoming else reminder.dtstart_local
    category = (
        await ctx.uow.categories.get(reminder.category_id, ctx.user.id)
        if reminder.category_id
        else None
    )
    text = texts.card(
        reminder,
        when=when,
        now_local=ctx.local_now,
        category=category,
        rule=reminder.schedule.recurrence,
        header=header,
    )
    await ctx.resp.edit(text, keyboards.card(reminder))


async def _load(ctx: flows.Ctx, reminder_id: int) -> Reminder | None:
    reminder = await ctx.uow.reminders.get(reminder_id, ctx.user.id)
    if reminder is None:
        await ctx.resp.send(texts.GONE)
    return reminder


@router.callback_query(cb.Card.filter(F.a.in_({"open", "back"})))
async def open_card(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    if (reminder := await _load(ctx, callback_data.r)) is not None:
        await render_card(ctx, reminder)


@router.callback_query(cb.Card.filter(F.a == "time"))
async def ask_time(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    if await _load(ctx, callback_data.r) is None:
        return
    await state.clear()
    await state.update_data({flows.PURPOSE_KEY: "edit_time", flows.TARGET_KEY: callback_data.r})
    await state.set_state(Edit.time)
    await ctx.resp.send(texts.ASK_NEW_TIME, keyboards.when_chips())


@router.message(Edit.time, F.text)
async def time_typed(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    result = await flows.parse_text(ctx, message.text or "")
    usable = flows.typed_time(result)
    if usable is None:
        reply = texts.PAST if Flag.PAST_DATE in result.flags else texts.NOT_UNDERSTOOD_TIME
        await message.answer(reply, reply_markup=keyboards.when_chips())
        return
    await flows.apply_time(ctx, *usable)


@router.callback_query(cb.Card.filter(F.a == "text"))
async def ask_text(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    if await _load(ctx, callback_data.r) is None:
        return
    await state.clear()
    await state.update_data({flows.TARGET_KEY: callback_data.r})
    await state.set_state(Edit.text)
    await ctx.resp.send(texts.ASK_NEW_TEXT)


@router.message(Edit.text, F.text)
async def text_typed(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    target = (await state.get_data()).get(flows.TARGET_KEY)
    if not isinstance(target, int):
        await state.clear()
        await message.answer(texts.GONE)
        return
    try:
        await actions.edit_text(uow, user.id, target, (message.text or "").strip(), ctx.now)
    except InvalidScheduleError:
        await message.answer(texts.TEXT_TOO_LONG)
        return
    except NotFoundError:
        await state.clear()
        await message.answer(texts.GONE)
        return
    await state.clear()
    reminder = await uow.reminders.get(target, user.id)
    if reminder is not None:
        await message.answer(texts.TEXT_UPDATED)
        await render_card(ctx, reminder)


@router.callback_query(cb.Card.filter(F.a == "cat"))
async def choose_category(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    if (reminder := await _load(ctx, callback_data.r)) is None:
        return
    cats = await uow.categories.list(user.id)
    await ctx.resp.edit(
        texts.PICK_CATEGORY, keyboards.category_picker(cats, reminder.id, reminder.category_id)
    )


@router.callback_query(cb.CardCat.filter())
async def category_chosen(
    query: CallbackQuery,
    callback_data: cb.CardCat,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    target = callback_data.c or None
    try:
        await actions.set_reminder_category(uow, user.id, callback_data.r, target, ctx.now)
    except NotFoundError:
        await ctx.resp.send(texts.GONE)
        return
    reminder = await uow.reminders.get(callback_data.r, user.id)
    if reminder is None:
        return
    if target is not None:  # an explicit choice is the strongest signal we get
        await categories.learn(uow, user.id, reminder.text, target)
    await render_card(ctx, reminder, "🗂 Категория обновлена")


@router.callback_query(cb.Card.filter(F.a == "del"))
async def delete(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    if (reminder := await _load(ctx, callback_data.r)) is None:
        return
    await actions.delete_reminder(uow, user.id, reminder.id, ctx.now)
    await ctx.resp.edit(
        texts.DELETED.format(text=h(reminder.text)), keyboards.after_delete(reminder.id)
    )


@router.callback_query(cb.Card.filter(F.a == "restore"))
async def restore(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    try:
        reminder = await actions.restore_reminder(uow, user.id, callback_data.r, ctx.now)
    except NotFoundError:
        await ctx.resp.send(texts.GONE)
        return
    await render_card(ctx, reminder, texts.RESTORED)


@router.callback_query(cb.Card.filter(F.a.in_({"pause", "resume"})))
async def pause_resume(
    query: CallbackQuery,
    callback_data: cb.Card,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    act = actions.pause_reminder if callback_data.a == "pause" else actions.resume_reminder
    try:
        await act(uow, user.id, callback_data.r, ctx.now)
    except ActionExpiredError:
        await ctx.resp.send(texts.EXPIRED)
    reminder = await uow.reminders.get(callback_data.r, user.id)
    if reminder is not None and reminder.status is not ReminderStatus.DELETED:
        header = texts.PAUSED if reminder.status is ReminderStatus.PAUSED else texts.RESUMED
        await render_card(ctx, reminder, header)
