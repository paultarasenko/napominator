from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from napominator.application.errors import InvalidScheduleError, LimitExceededError
from napominator.application.reminders import create_reminder
from napominator.application.scheduling import plan_occurrence
from napominator.domain.entities import (
    MAX_ACTIVE_REMINDERS,
    NewReminder,
    NotificationKind,
    NotificationStatus,
)
from tests.support import T0, UowFactory, add_reminder, add_user


def new(user_id: int, **kw: object) -> NewReminder:
    base: dict[str, object] = {
        "user_id": user_id,
        "text": "Позвонить маме",
        "tz": "Europe/Moscow",
        "dtstart_local": datetime(2026, 10, 14, 18, 0),  # tomorrow 18:00 Moscow == 15:00Z
    }
    base.update(kw)
    return NewReminder(**base)  # type: ignore[arg-type]


async def test_one_off_creates_the_reminder_and_its_due_notification(
    uow_factory: UowFactory,
) -> None:
    user = await add_user(uow_factory)
    async with uow_factory() as uow:
        created = await create_reminder(uow, new(user.id), T0)
    assert created.first_occurrence == datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    async with uow_factory() as uow:
        (note,) = await uow.notifications.list_for_reminder(created.reminder.id)
    assert (note.kind, note.status, note.fire_at) == (
        NotificationKind.DUE,
        NotificationStatus.PENDING,
        created.first_occurrence,
    )


async def test_recurring_schedules_only_the_next_occurrence(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    async with uow_factory() as uow:
        created = await create_reminder(
            uow,
            new(user.id, dtstart_local=datetime(2026, 10, 19, 9, 0), rrule="FREQ=WEEKLY;BYDAY=MO"),
            T0,
        )
        rows = await uow.notifications.list_for_reminder(created.reminder.id)
    assert len(rows) == 1
    assert rows[0].fire_at == datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


async def test_lead_notifications_are_planned_only_when_still_in_the_future(
    uow_factory: UowFactory,
) -> None:
    user = await add_user(uow_factory)
    soon = T0 + timedelta(minutes=30)  # a 60-minute lead would already be in the past
    local = (soon + timedelta(hours=3)).replace(tzinfo=None)
    async with uow_factory() as uow:
        created = await create_reminder(
            uow, new(user.id, dtstart_local=local, lead_minutes=(10, 60, 1440)), T0
        )
        rows = await uow.notifications.list_for_reminder(created.reminder.id)
    assert [(r.kind, r.fire_at) for r in rows] == [
        (NotificationKind.LEAD, soon - timedelta(minutes=10)),
        (NotificationKind.DUE, soon),
    ]


def test_plan_occurrence_keys_are_stable_and_distinct() -> None:
    from napominator.domain.entities import Reminder, ReminderStatus  # noqa: PLC0415

    reminder = Reminder(
        id=7, user_id=3, category_id=None, text="x", tz="UTC",
        dtstart_local=datetime(2026, 1, 1), rrule=None, lead_minutes=(60, 60, 5),
        nag_interval_min=None, nag_max=None, status=ReminderStatus.ACTIVE, deleted_at=None,
        completed_at=None, source_message_id=None, created_at=T0, updated_at=T0,
    )  # fmt: skip
    when = T0 + timedelta(days=2)
    first = plan_occurrence(reminder, when, T0)
    again = plan_occurrence(reminder, when, T0)
    assert [n.dedup_key for n in first] == [n.dedup_key for n in again]
    assert len({n.dedup_key for n in first}) == len(first) == 3  # due + two distinct leads


async def test_a_reminder_with_no_future_occurrence_is_rejected_and_not_stored(
    uow_factory: UowFactory,
) -> None:
    user = await add_user(uow_factory)
    with pytest.raises(InvalidScheduleError, match="future"):
        async with uow_factory() as uow:
            await create_reminder(uow, new(user.id, dtstart_local=datetime(2026, 10, 1, 9, 0)), T0)
    async with uow_factory() as uow:
        assert await uow.reminders.count_active(user.id) == 0


async def test_a_corrupt_rrule_is_a_schedule_error(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    with pytest.raises(InvalidScheduleError):
        async with uow_factory() as uow:
            await create_reminder(uow, new(user.id, rrule="FREQ=NEVER"), T0)


@pytest.mark.parametrize("text", ["", "x" * 1001])
async def test_text_length_is_enforced(uow_factory: UowFactory, text: str) -> None:
    user = await add_user(uow_factory)
    with pytest.raises(InvalidScheduleError, match="characters"):
        async with uow_factory() as uow:
            await create_reminder(uow, new(user.id, text=text), T0)


async def test_the_active_reminder_cap_is_enforced(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    async with uow_factory() as uow:
        for _ in range(MAX_ACTIVE_REMINDERS):
            await uow.reminders.add(new(user.id), T0)
    with pytest.raises(LimitExceededError) as info:
        async with uow_factory() as uow:
            await create_reminder(uow, new(user.id), T0)
    assert info.value.limit == MAX_ACTIVE_REMINDERS
    other = await add_user(uow_factory, 2)
    async with uow_factory() as uow:  # the cap is per user
        await create_reminder(uow, new(other.id), T0)


async def test_unused_builder_is_importable(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    assert (await add_reminder(uow_factory, user)).user_id == user.id
