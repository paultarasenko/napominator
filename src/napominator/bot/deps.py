"""Dependencies handed to handlers (aiogram injects ``deps`` by parameter name)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from napominator.application.ports import Clock, ReminderParser, UnitOfWorkFactory
from napominator.infrastructure.tzlookup import TimezoneResolver


@dataclass(frozen=True, slots=True)
class AppDeps:
    uow_factory: UnitOfWorkFactory
    clock: Clock
    parser: ReminderParser
    tz: TimezoneResolver
    wake: Callable[[], None]
    admin_ids: frozenset[int] = frozenset()
    llm_enabled: bool = False
