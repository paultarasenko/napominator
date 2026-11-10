"""Turning a reminder's schedule into outbox rows."""

from __future__ import annotations

from datetime import datetime, timedelta

from napominator.application.errors import InvalidScheduleError
from napominator.application.ports import UnitOfWork
from napominator.domain.entities import (
    NewNotification,
    NotificationKind,
    Reminder,
    dedup_key,
)
from napominator.domain.recurrence import InvalidRecurrenceError, Schedule, next_occurrence


def reminder_schedule(reminder: Reminder) -> Schedule:
    try:
        return reminder.schedule
    except (InvalidRecurrenceError, ValueError) as exc:
        raise InvalidScheduleError(str(exc)) from exc


def plan_occurrence(
    reminder: Reminder, occurrence: datetime, now: datetime
) -> list[NewNotification]:
    """The due notification plus any "заранее" notifications that are still in the future."""
    planned = [
        NewNotification(
            user_id=reminder.user_id,
            reminder_id=reminder.id,
            kind=NotificationKind.DUE,
            occurrence_at=occurrence,
            fire_at=occurrence,
            dedup_key=dedup_key(
                NotificationKind.DUE,
                occurrence,
                reminder_id=reminder.id,
                user_id=reminder.user_id,
            ),
        )
    ]
    for minutes in sorted(set(reminder.lead_minutes)):
        fire_at = occurrence - timedelta(minutes=minutes)
        if fire_at > now:
            planned.append(
                NewNotification(
                    user_id=reminder.user_id,
                    reminder_id=reminder.id,
                    kind=NotificationKind.LEAD,
                    occurrence_at=occurrence,
                    fire_at=fire_at,
                    dedup_key=dedup_key(
                        NotificationKind.LEAD,
                        occurrence,
                        reminder_id=reminder.id,
                        user_id=reminder.user_id,
                        seq=minutes,
                    ),
                    seq=minutes,
                )
            )
    return planned


async def schedule_next(
    uow: UnitOfWork, reminder: Reminder, *, after: datetime, now: datetime
) -> datetime | None:
    """Materialise the next occurrence strictly after ``after``; ``None`` if the series ended.

    Idempotent: the dedup key makes a repeated call a no-op.
    """
    occurrence = next_occurrence(reminder_schedule(reminder), after)
    if occurrence is None:
        return None
    for notification in plan_occurrence(reminder, occurrence, now):
        await uow.notifications.add(notification, now)
    return occurrence
