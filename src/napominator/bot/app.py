"""Builds the aiogram Dispatcher: middlewares, routers, error handling."""

from __future__ import annotations

from aiogram import Dispatcher, Router
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from napominator.bot.deps import AppDeps
from napominator.bot.handlers import (
    ack,
    cards,
    categories,
    create,
    lists,
    pickers,
    privacy,
    settings,
    start,
)
from napominator.bot.middlewares import SessionMiddleware, ThrottleMiddleware, report_error

COMMANDS = (
    BotCommand(command="new", description="Создать напоминание"),
    BotCommand(command="today", description="Что сегодня"),
    BotCommand(command="list", description="Все напоминания"),
    BotCommand(command="categories", description="Категории"),
    BotCommand(command="search", description="Найти напоминание"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="privacy", description="Какие данные я храню"),
    BotCommand(command="export", description="Выгрузить мои данные"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить действие"),
)


def _fresh(router: Router) -> Router:
    """A new Router carrying the same handlers.

    aiogram lets a Router join one Dispatcher only; copying lets us build a Dispatcher per
    call (tests, restarts) while handlers stay plain module-level functions.
    """
    clone = Router(name=router.name)
    for name, observer in router.observers.items():
        clone.observers[name].handlers.extend(observer.handlers)
    return clone


def build_dispatcher(
    deps: AppDeps, storage: BaseStorage | None = None, *, throttle: bool = True
) -> Dispatcher:
    dp = Dispatcher(storage=storage or MemoryStorage())
    if throttle:
        dp.message.outer_middleware(ThrottleMiddleware(deps))
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(SessionMiddleware(deps))
    # order matters: specific flows first, the free-text catch-all (create) last
    dp.include_routers(
        *(
            _fresh(r)
            for r in (
                start.router,
                lists.router,
                categories.router,
                settings.router,
                privacy.router,
                pickers.router,
                cards.router,
                ack.router,
                create.router,
            )
        )
    )
    dp.errors.register(report_error)
    return dp
