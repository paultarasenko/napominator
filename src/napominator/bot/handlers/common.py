"""Small helpers shared by handlers."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from napominator.application.ports import UnitOfWork
from napominator.bot import keyboards, texts
from napominator.bot.deps import AppDeps
from napominator.bot.flows import Ctx
from napominator.bot.respond import Responder
from napominator.domain.entities import User


def make_ctx(
    event: Message | CallbackQuery, state: FSMContext, deps: AppDeps, uow: UnitOfWork, user: User
) -> Ctx:
    return Ctx(Responder(event), state, deps, uow, user)


async def require_onboarding(ctx: Ctx) -> bool:
    """``True`` when the user can proceed; otherwise starts the time-zone step and returns False."""
    if ctx.user.timezone is not None:
        return True
    await ctx.state.clear()
    await ctx.state.update_data(flow="onboarding")
    await ctx.resp.send(texts.TZ_NEED_FIRST, keyboards.timezone_methods())
    return False
