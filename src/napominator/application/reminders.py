"""Reminder use cases that touch the schedule (creation, user reactivation)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from napominator.application.errors import InvalidScheduleError, LimitExceededError
from napominator.application.ports import UnitOfWork
from napominator.application.scheduling import schedule_next
from napominator.domain.entities import (
    MAX_ACTIVE_REMINDERS,
    MAX_TEXT_LENGTH,
    NewReminder,
    Reminder,
)


@dataclass(frozen=True, slots=True)
class CreatedReminder:
    reminder: Reminder
    first_occurrence: datetime  # aware UTC


async def create_reminder(uow: UnitOfWork, new: NewReminder, now: datetime) -> CreatedReminder:
    """Validate, store and schedule a reminder inside the caller's transaction.

    Raises ``LimitExceededError`` / ``InvalidScheduleError`` (the caller's transaction then
    rolls back, so nothing is half-created).
    """
    if not 1 <= len(new.text) <= MAX_TEXT_LENGTH:
        raise InvalidScheduleError(f"text must be 1..{MAX_TEXT_LENGTH} characters")
    if await uow.reminders.count_active(new.user_id) >= MAX_ACTIVE_REMINDERS:
        raise LimitExceededError("active reminders", MAX_ACTIVE_REMINDERS)
    reminder = await uow.reminders.add(new, now)
    first = await schedule_next(uow, reminder, after=now, now=now)
    if first is None:
        raise InvalidScheduleError("the reminder has no occurrence in the future")
    return CreatedReminder(reminder, first)


async def reactivate_user(uow: UnitOfWork, user_id: int, now: datetime) -> int:
    """The user is back (``/start`` after blocking the bot): resume every repeating series.

    Returns how many series were re-scheduled. One-off reminders that were skipped while the
    bot was blocked are not resurrected.
    """
    await uow.users.set_active(user_id, active=True)
    resumed = 0
    for reminder in await uow.reminders.recurring_without_open_notification(user_id):
        if await schedule_next(uow, reminder, after=now, now=now) is not None:
            resumed += 1
    return resumed
