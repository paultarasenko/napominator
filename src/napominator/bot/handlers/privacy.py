"""/privacy, /export, /delete_me and the admin overview."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message, ReplyKeyboardRemove

from napominator.application import privacy
from napominator.application.ports import UnitOfWork
from napominator.bot import callbacks as cb
from napominator.bot import keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.domain.entities import User

router = Router(name="privacy")


@router.message(Command("privacy"))
async def privacy_command(message: Message, deps: AppDeps) -> None:
    await message.answer(
        texts.privacy(llm_enabled=deps.llm_enabled), reply_markup=keyboards.privacy_actions()
    )


@router.callback_query(cb.Nav.filter(F.to == "privacy"))
async def privacy_button(query: CallbackQuery, deps: AppDeps) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(
            texts.privacy(llm_enabled=deps.llm_enabled), reply_markup=keyboards.privacy_actions()
        )


async def _send_export(
    event: Message | CallbackQuery, deps: AppDeps, uow: UnitOfWork, user: User
) -> None:
    now = deps.clock.now()
    data = await privacy.export_user(uow, user, now)
    stamp = now.strftime("%Y%m%d")
    file = BufferedInputFile(privacy.to_json_bytes(data), filename=f"napominator-{stamp}.json")
    message = event if isinstance(event, Message) else event.message
    if isinstance(message, Message):
        await message.answer_document(file, caption=texts.EXPORT_CAPTION)


@router.message(Command("export"))
async def export_command(message: Message, deps: AppDeps, uow: UnitOfWork, user: User) -> None:
    await _send_export(message, deps, uow, user)


@router.callback_query(cb.Privacy.filter(F.a == "export"))
async def export_button(query: CallbackQuery, deps: AppDeps, uow: UnitOfWork, user: User) -> None:
    await query.answer()
    await _send_export(query, deps, uow, user)


@router.message(Command("delete_me"))
async def delete_command(message: Message, uow: UnitOfWork, user: User) -> None:
    summary = await privacy.deletion_preview(uow, user.id)
    await message.answer(
        texts.delete_confirm(summary.reminders, summary.categories),
        reply_markup=keyboards.delete_account_confirm(),
    )


@router.callback_query(cb.Privacy.filter(F.a == "delete"))
async def delete_button(query: CallbackQuery, uow: UnitOfWork, user: User) -> None:
    await query.answer()
    summary = await privacy.deletion_preview(uow, user.id)
    if isinstance(query.message, Message):
        await query.message.answer(
            texts.delete_confirm(summary.reminders, summary.categories),
            reply_markup=keyboards.delete_account_confirm(),
        )


@router.callback_query(cb.Privacy.filter(F.a == "delete_yes"))
async def delete_confirmed(
    query: CallbackQuery, state: FSMContext, uow: UnitOfWork, user: User
) -> None:
    await query.answer()
    await privacy.delete_account(uow, user.id)
    await state.clear()
    if isinstance(query.message, Message):
        await query.message.answer(texts.DELETE_DONE, reply_markup=ReplyKeyboardRemove())


@router.callback_query(cb.Privacy.filter(F.a == "delete_no"))
async def delete_cancelled(query: CallbackQuery) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await query.message.answer(texts.CANCELLED)


@router.message(Command("admin"))
async def admin_command(message: Message, deps: AppDeps, uow: UnitOfWork, user: User) -> None:
    if user.telegram_id not in deps.admin_ids:
        await message.answer("Не знаю такой команды. Напиши /help — там всё, что я умею.")
        return
    snapshot = await uow.admin.snapshot(deps.clock.now())
    await message.answer(texts.admin_overview(snapshot))
