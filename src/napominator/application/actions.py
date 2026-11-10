"""Actions on existing reminders and notifications (buttons and edit screens)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from napominator.application.errors import (
    ActionExpiredError,
    InvalidScheduleError,
    NotFoundError,
)
from napominator.application.ports import UnitOfWork
from napominator.application.scheduling import schedule_next
from napominator.domain.entities import (
    NewNotification,
    NotificationKind,
    NotificationStatus,
    Reminder,
    ReminderStatus,
    User,
    dedup_key,
)
from napominator.domain.recurrence import (
    InvalidRecurrenceError,
    Recurrence,
    Schedule,
    next_occurrence,
)
from napominator.domain.timeutil import load_zone, local_to_utc, utc_to_local

UNDO_WINDOW = timedelta(minutes=15)
ACK_RETENTION = timedelta(days=14)
_LIVE = (ReminderStatus.ACTIVE, ReminderStatus.PAUSED)
_OPEN_KINDS = (
    NotificationKind.DUE,
    NotificationKind.LEAD,
    NotificationKind.NAG,
    NotificationKind.SNOOZE,
)


async def _own_reminder(uow: UnitOfWork, user_id: int, reminder_id: int) -> Reminder:
    reminder = await uow.reminders.get(reminder_id, user_id)
    if reminder is None:
        raise NotFoundError("reminder")
    return reminder


# --- reacting to a delivered notification ---


async def mark_done(uow: UnitOfWork, user_id: int, notification_id: int, now: datetime) -> None:
    """ "✅ Готово". Idempotent: pressing it twice changes nothing."""
    note = await uow.notifications.get_for_user(notification_id, user_id)
    if note is None:
        raise NotFoundError("notification")
    if note.status is NotificationStatus.DONE:
        return
    changed = await uow.notifications.transition(
        notification_id,
        user_id,
        expected=[NotificationStatus.SENT, NotificationStatus.SNOOZED],
        new=NotificationStatus.DONE,
        acked_at=now,
    )
    if not changed:
        raise ActionExpiredError("notification is no longer awaiting a reaction")
    if note.status is NotificationStatus.SNOOZED:  # finishing early cancels the queued repeat
        await uow.notifications.skip_open(note.reminder_id or 0, [NotificationKind.SNOOZE], "done")


async def snooze(
    uow: UnitOfWork, user_id: int, notification_id: int, until: datetime, now: datetime
) -> datetime:
    """ "⏰ Отложить": queue a ``snooze`` notification at ``until`` (aware UTC)."""
    note = await uow.notifications.get_for_user(notification_id, user_id)
    if note is None or note.reminder_id is None:
        raise NotFoundError("notification")
    if until <= now:
        raise InvalidScheduleError("snooze time must be in the future")
    reminder = await _own_reminder(uow, user_id, note.reminder_id)
    if reminder.status is ReminderStatus.DELETED:
        raise ActionExpiredError("reminder was deleted")
    changed = await uow.notifications.transition(
        notification_id,
        user_id,
        expected=[NotificationStatus.SENT, NotificationStatus.DONE],
        new=NotificationStatus.SNOOZED,
        acked_at=now,
    )
    if not changed:
        raise ActionExpiredError("notification is no longer awaiting a reaction")
    if reminder.status is ReminderStatus.DONE:  # a closed one-off gets one more delivery
        await uow.reminders.transition(
            reminder.id, user_id, expected=[ReminderStatus.DONE], new=ReminderStatus.ACTIVE, now=now
        )
        await uow.reminders.update(reminder.id, user_id, now, completed_at=None)
    await uow.notifications.add(
        NewNotification(
            user_id=user_id,
            reminder_id=reminder.id,
            kind=NotificationKind.SNOOZE,
            occurrence_at=note.occurrence_at,
            fire_at=until,
            dedup_key=dedup_key(
                NotificationKind.SNOOZE,
                note.occurrence_at,
                reminder_id=reminder.id,
                user_id=user_id,
                seq=note.id,
            ),
            seq=note.id,
        ),
        now,
    )
    return until


async def undo_reaction(uow: UnitOfWork, user_id: int, notification_id: int, now: datetime) -> None:
    """ "↩️ Отменить" right after Done/Snooze (``UNDO_WINDOW``)."""
    note = await uow.notifications.get_for_user(notification_id, user_id)
    if note is None:
        raise NotFoundError("notification")
    if note.acked_at is None or now - note.acked_at > UNDO_WINDOW:
        raise ActionExpiredError("too late to undo")
    if note.status is NotificationStatus.SNOOZED and note.reminder_id is not None:
        await uow.notifications.skip_open(note.reminder_id, [NotificationKind.SNOOZE], "undo")
    elif note.status is not NotificationStatus.DONE:
        raise ActionExpiredError("nothing to undo")
    await uow.notifications.transition(
        notification_id,
        user_id,
        expected=[NotificationStatus.DONE, NotificationStatus.SNOOZED],
        new=NotificationStatus.SENT,
        acked_at=None,
    )


def snooze_target(
    now: datetime, tz_name: str, *, choice: str, evening: datetime | None = None
) -> datetime:
    """Resolve a quick-snooze choice (``15m``/``1h``/``3h``/``tomorrow``/``evening``) to UTC."""
    tz = load_zone(tz_name)
    local = utc_to_local(now, tz)
    if choice == "15m":
        return now + timedelta(minutes=15)
    if choice == "1h":
        return now + timedelta(hours=1)
    if choice == "3h":
        return now + timedelta(hours=3)
    if choice == "evening" and evening is not None:
        return evening
    if choice == "tomorrow":
        tomorrow = (local + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return local_to_utc(tomorrow, tz)
    raise InvalidScheduleError(f"unknown snooze choice: {choice}")


# --- lifecycle of a reminder ---


async def delete_reminder(uow: UnitOfWork, user_id: int, reminder_id: int, now: datetime) -> None:
    reminder = await _own_reminder(uow, user_id, reminder_id)
    if reminder.status is ReminderStatus.DELETED:
        return
    await uow.reminders.transition(
        reminder_id, user_id, expected=[reminder.status], new=ReminderStatus.DELETED, now=now
    )
    await uow.notifications.skip_open(reminder_id, _OPEN_KINDS, "reminder_deleted")


async def restore_reminder(
    uow: UnitOfWork, user_id: int, reminder_id: int, now: datetime
) -> Reminder:
    """ "↩️ Вернуть": bring back a deleted reminder and its schedule."""
    reminder = await _own_reminder(uow, user_id, reminder_id)
    if reminder.status is not ReminderStatus.DELETED:
        return reminder
    nxt = next_occurrence(reminder.schedule, now)
    status = ReminderStatus.ACTIVE if nxt is not None else ReminderStatus.DONE
    await uow.reminders.transition(
        reminder_id, user_id, expected=[ReminderStatus.DELETED], new=status, now=now
    )
    await uow.reminders.update(reminder_id, user_id, now, completed_at=None)
    restored = await _own_reminder(uow, user_id, reminder_id)
    if status is ReminderStatus.ACTIVE:
        await schedule_next(uow, restored, after=now, now=now)
    return restored


async def pause_reminder(uow: UnitOfWork, user_id: int, reminder_id: int, now: datetime) -> None:
    changed = await uow.reminders.transition(
        reminder_id, user_id, expected=[ReminderStatus.ACTIVE], new=ReminderStatus.PAUSED, now=now
    )
    if not changed:
        raise ActionExpiredError("reminder is not active")
    await uow.notifications.skip_open(reminder_id, _OPEN_KINDS, "reminder_paused")


async def resume_reminder(uow: UnitOfWork, user_id: int, reminder_id: int, now: datetime) -> None:
    changed = await uow.reminders.transition(
        reminder_id, user_id, expected=[ReminderStatus.PAUSED], new=ReminderStatus.ACTIVE, now=now
    )
    if not changed:
        raise ActionExpiredError("reminder is not paused")
    reminder = await _own_reminder(uow, user_id, reminder_id)
    if await schedule_next(uow, reminder, after=now, now=now) is None:
        await uow.reminders.transition(
            reminder_id, user_id, expected=[ReminderStatus.ACTIVE], new=ReminderStatus.DONE, now=now
        )


async def edit_text(
    uow: UnitOfWork, user_id: int, reminder_id: int, text: str, now: datetime
) -> None:
    if not 1 <= len(text) <= 1000:  # noqa: PLR2004
        raise InvalidScheduleError("text must be 1..1000 characters")
    if not await uow.reminders.update(reminder_id, user_id, now, text=text):
        raise NotFoundError("reminder")


async def set_reminder_category(
    uow: UnitOfWork, user_id: int, reminder_id: int, category_id: int | None, now: datetime
) -> None:
    if category_id is not None and await uow.categories.get(category_id, user_id) is None:
        raise NotFoundError("category")
    if not await uow.reminders.update(reminder_id, user_id, now, category_id=category_id):
        raise NotFoundError("reminder")


@dataclass(frozen=True, slots=True)
class Rescheduled:
    reminder: Reminder
    first_occurrence: datetime


async def reschedule(
    uow: UnitOfWork,
    user_id: int,
    reminder_id: int,
    *,
    dtstart_local: datetime,
    rrule: str | None,
    now: datetime,
) -> Rescheduled:
    """New time and optionally a new rule: drop the queued notifications, queue new ones."""
    reminder = await _own_reminder(uow, user_id, reminder_id)
    if reminder.status is ReminderStatus.DELETED:
        raise NotFoundError("reminder")
    candidate = Schedule(dtstart_local, load_zone(reminder.tz), _rule(rrule))
    first = next_occurrence(candidate, now)
    if first is None:
        raise InvalidScheduleError("the new time has no occurrence in the future")
    await uow.notifications.skip_open(reminder_id, _OPEN_KINDS, "rescheduled")
    await uow.reminders.update(
        reminder_id, user_id, now, dtstart_local=dtstart_local, rrule=rrule, completed_at=None
    )
    if reminder.status is not ReminderStatus.PAUSED:
        await uow.reminders.transition(
            reminder_id,
            user_id,
            expected=[ReminderStatus.ACTIVE, ReminderStatus.DONE],
            new=ReminderStatus.ACTIVE,
            now=now,
        )
    updated = await _own_reminder(uow, user_id, reminder_id)
    if updated.status is ReminderStatus.ACTIVE:
        await schedule_next(uow, updated, after=now, now=now)
    return Rescheduled(updated, first)


def _rule(rrule: str | None) -> Recurrence | None:
    if rrule is None:
        return None
    try:
        return Recurrence.from_rrule(rrule)
    except InvalidRecurrenceError as exc:
        raise InvalidScheduleError(str(exc)) from exc


# --- time zone change ---


async def change_timezone(
    uow: UnitOfWork, user: User, new_tz: str, *, keep_local_time: bool, now: datetime
) -> int:
    """Switch the user's zone and re-plan every live reminder (spec S10).

    ``keep_local_time=True``  - "10:00 stays 10:00 on the wall clock" (you moved).
    ``keep_local_time=False`` - the absolute moment stays (your phone just shows another zone).
    Returns how many reminders were re-planned.
    """
    load_zone(new_tz)  # validates
    await uow.users.update(user.id, timezone=new_tz)
    replanned = 0
    for reminder in await uow.reminders.list_live(user.id):
        dtstart = reminder.dtstart_local
        if not keep_local_time and reminder.tz != new_tz:
            old_tz, target = load_zone(reminder.tz), load_zone(new_tz)
            dtstart = utc_to_local(local_to_utc(dtstart, old_tz), target)
        await uow.notifications.skip_open(reminder.id, _OPEN_KINDS, "timezone_changed")
        await uow.reminders.update(reminder.id, user.id, now, tz=new_tz, dtstart_local=dtstart)
        if reminder.status is ReminderStatus.ACTIVE:
            fresh = await _own_reminder(uow, user.id, reminder.id)
            if await schedule_next(uow, fresh, after=now, now=now) is None:
                await uow.reminders.transition(
                    reminder.id,
                    user.id,
                    expected=[ReminderStatus.ACTIVE],
                    new=ReminderStatus.DONE,
                    now=now,
                )
        replanned += 1
    return replanned
