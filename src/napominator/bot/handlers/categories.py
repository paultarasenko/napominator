"""Category management screens."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application import categories
from napominator.application.errors import (
    DuplicateCategoryError,
    LimitExceededError,
    NotFoundError,
)
from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import flows, keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.handlers.common import make_ctx, require_onboarding
from napominator.bot.states import Cat
from napominator.domain.categories import InvalidCategoryNameError
from napominator.domain.entities import User

router = Router(name="categories")
TARGET = "category_target"


async def show_categories(ctx: flows.Ctx, *, edit: bool) -> None:
    cats = await ctx.uow.categories.list(ctx.user.id)
    counts = {c.id: await ctx.uow.categories.count_reminders(c.id, ctx.user.id) for c in cats}
    markup = keyboards.categories_screen(cats, counts)
    if edit:
        await ctx.resp.edit(texts.CATEGORIES_TITLE, markup)
    else:
        await ctx.resp.send(texts.CATEGORIES_TITLE, markup)


@router.message(Command("categories"))
@router.message(F.text == keyboards.BTN_CATEGORIES)
async def categories_command(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    if await require_onboarding(ctx):
        await state.clear()
        await show_categories(ctx, edit=False)


@router.callback_query(cb.Nav.filter(F.to == "cats"))
async def categories_button(
    query: CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    await state.clear()
    await show_categories(ctx, edit=True)


@router.callback_query(cb.CatMenu.filter())
async def category_action(
    query: CallbackQuery,
    callback_data: cb.CatMenu,
    state: FSMContext,
    deps: AppDeps,
    uow: UnitOfWork,
    user: User,
) -> None:
    ctx = make_ctx(query, state, deps, uow, user)
    await ctx.resp.ack()
    action = callback_data.a
    if action == "new":
        await state.set_state(Cat.new)
        await ctx.resp.send(texts.CAT_ASK_NEW)
        return
    category = await uow.categories.get(callback_data.c, user.id)
    if category is None:
        await ctx.resp.send(texts.GONE)
        return
    reminders = await uow.categories.count_reminders(category.id, user.id)
    if action == "open":
        await state.clear()
        await ctx.resp.edit(
            texts.category_menu_text(category, reminders), keyboards.category_menu(category)
        )
    elif action in {"rename", "emoji"}:
        await state.update_data({TARGET: category.id})
        await state.set_state(Cat.rename if action == "rename" else Cat.emoji)
        await ctx.resp.send(texts.CAT_ASK_RENAME if action == "rename" else texts.CAT_ASK_EMOJI)
    elif action in {"up", "down"}:
        await uow.categories.swap(user.id, category.id, -1 if action == "up" else 1)
        await show_categories(ctx, edit=True)
    elif action == "del":
        others = [c for c in await uow.categories.list(user.id) if c.id != category.id]
        await ctx.resp.edit(
            texts.category_delete_text(category, reminders),
            keyboards.delete_category_targets(category, others, reminders),
        )
    elif action in {"delgo", "delmove"}:
        moved = await categories.delete_category(
            uow, user.id, category.id, move_to=callback_data.t or None
        )
        await ctx.resp.send(texts.CAT_DELETED.format(n=moved))
        await show_categories(ctx, edit=False)


async def _input_error(ctx: flows.Ctx, exc: Exception) -> bool:
    """Report a rejected name; ``True`` when the flow should end instead of re-asking."""
    if isinstance(exc, DuplicateCategoryError):
        await ctx.resp.send(texts.CAT_DUPLICATE)
    elif isinstance(exc, LimitExceededError):
        await ctx.state.clear()
        await ctx.resp.send(texts.CAT_LIMIT)
        return True
    elif isinstance(exc, InvalidCategoryNameError):
        await ctx.resp.send(texts.CAT_BAD_NAME)
    else:
        await ctx.state.clear()
        await ctx.resp.send(texts.GONE)
        return True
    return False


@router.message(Cat.new, F.text)
async def name_new(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    try:
        await categories.create_category(uow, user.id, message.text or "")
    except (DuplicateCategoryError, LimitExceededError, InvalidCategoryNameError) as exc:
        await _input_error(ctx, exc)
        return
    await state.clear()
    await show_categories(ctx, edit=False)


@router.message(Cat.rename, F.text)
async def name_rename(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    target = (await state.get_data()).get(TARGET)
    if not isinstance(target, int):
        await state.clear()
        await message.answer(texts.GONE)
        return
    try:
        await categories.rename_category(uow, user.id, target, message.text or "")
    except (DuplicateCategoryError, NotFoundError, InvalidCategoryNameError) as exc:
        await _input_error(ctx, exc)
        return
    await state.clear()
    await show_categories(ctx, edit=False)


@router.message(Cat.emoji, F.text)
async def emoji_set(
    message: Message, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    ctx = make_ctx(message, state, deps, uow, user)
    target = (await state.get_data()).get(TARGET)
    if not isinstance(target, int):
        await state.clear()
        await message.answer(texts.GONE)
        return
    try:
        await categories.set_category_emoji(uow, user.id, target, message.text or "")
    except NotFoundError:
        await message.answer(texts.CAT_NO_EMOJI)
        return
    await state.clear()
    await show_categories(ctx, edit=False)
