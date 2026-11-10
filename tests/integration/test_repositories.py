"""Repository behaviour on every supported dialect (SQLite always, PostgreSQL when available)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from napominator.domain.entities import (
    NotificationKind,
    NotificationStatus,
    ReminderStatus,
)
from tests.support import T0, UowFactory, add_reminder, add_user, new_notification

LEASE = timedelta(seconds=60)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


async def test_user_roundtrip(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory, 42)
    async with uow_factory() as uow:
        by_id = await uow.users.get(user.id)
        by_tg = await uow.users.get_by_telegram_id(42)
        missing = await uow.users.get_by_telegram_id(43)
    assert by_id == by_tg == user
    assert missing is None
    assert user.is_active
    assert user.timezone is None
    assert (user.morning_time.hour, user.evening_time.hour) == (9, 19)
    assert user.created_at == T0


async def test_reminder_lookup_is_owner_checked(uow_factory: UowFactory) -> None:
    alice = await add_user(uow_factory, 1)
    bob = await add_user(uow_factory, 2)
    reminder = await add_reminder(uow_factory, alice, lead_minutes=(60, 1440))
    async with uow_factory() as uow:
        own = await uow.reminders.get(reminder.id, alice.id)
        foreign = await uow.reminders.get(reminder.id, bob.id)
    assert own == reminder
    assert own is not None
    assert own.lead_minutes == (60, 1440)
    assert own.dtstart_local.tzinfo is None  # wall-clock value survives untouched
    assert foreign is None


async def test_transition_is_conditional_and_owner_checked(uow_factory: UowFactory) -> None:
    alice = await add_user(uow_factory, 1)
    bob = await add_user(uow_factory, 2)
    reminder = await add_reminder(uow_factory, alice)
    async with uow_factory() as uow:
        stranger = await uow.reminders.transition(
            reminder.id,
            bob.id,
            expected=[ReminderStatus.ACTIVE],
            new=ReminderStatus.DELETED,
            now=at(1),
        )
        first = await uow.reminders.transition(
            reminder.id,
            alice.id,
            expected=[ReminderStatus.ACTIVE],
            new=ReminderStatus.DELETED,
            now=at(1),
        )
        second = await uow.reminders.transition(
            reminder.id,
            alice.id,
            expected=[ReminderStatus.ACTIVE],
            new=ReminderStatus.DELETED,
            now=at(2),
        )
    assert (stranger, first, second) == (False, True, False)
    async with uow_factory() as uow:
        stored = await uow.reminders.get(reminder.id, alice.id)
    assert stored is not None
    assert stored.status is ReminderStatus.DELETED
    assert stored.deleted_at == at(1)


async def test_count_active_ignores_finished_and_foreign(uow_factory: UowFactory) -> None:
    alice = await add_user(uow_factory, 1)
    bob = await add_user(uow_factory, 2)
    done = await add_reminder(uow_factory, alice)
    await add_reminder(uow_factory, alice)
    await add_reminder(uow_factory, bob)
    async with uow_factory() as uow:
        await uow.reminders.transition(
            done.id, alice.id, expected=[ReminderStatus.ACTIVE], new=ReminderStatus.DONE, now=at(1)
        )
        assert await uow.reminders.count_active(alice.id) == 1


async def test_notification_insert_is_idempotent(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    note = new_notification(user, reminder, at(5))
    async with uow_factory() as uow:
        assert await uow.notifications.add(note, T0) is True
        assert await uow.notifications.add(note, T0) is False
    async with uow_factory() as uow:
        assert len(await uow.notifications.list_for_reminder(reminder.id)) == 1


async def test_rescheduling_revives_only_skipped_rows(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    skipped_note = new_notification(user, reminder, at(5))
    sent_note = new_notification(user, reminder, at(6))
    async with uow_factory() as uow:
        await uow.notifications.add(skipped_note, T0)
        await uow.notifications.add(sent_note, T0)
        await uow.notifications.skip_open_for_reminder(reminder.id, "recipient_blocked")
    async with uow_factory() as uow:
        rows = await uow.notifications.list_for_reminder(reminder.id)
        (second,) = [r for r in rows if r.fire_at == at(6)]
        await uow.notifications.claim_due(at(10), 10, LEASE)  # nothing claimable: both skipped
        # make the second one genuinely sent, so it must stay untouched
        await uow.notifications.add(new_notification(user, reminder, at(7)), T0)
        (third,) = [
            r for r in await uow.notifications.list_for_reminder(reminder.id) if r.fire_at == at(7)
        ]
        (claimed,) = await uow.notifications.claim_due(at(10), 10, LEASE)
        await uow.notifications.finish(
            claimed.notification.id,
            attempts=1,
            status=NotificationStatus.SENT,
            now=at(10),
            message_id=9,
        )
    assert second.status is NotificationStatus.SKIPPED
    assert third.status is NotificationStatus.PENDING
    async with uow_factory() as uow:
        assert await uow.notifications.add(skipped_note, at(20)) is True  # revived
        assert await uow.notifications.add(new_notification(user, reminder, at(7)), at(20)) is False
        by_time = {r.fire_at: r for r in await uow.notifications.list_for_reminder(reminder.id)}
    assert by_time[at(5)].status is NotificationStatus.PENDING
    assert by_time[at(5)].attempts == 0
    assert by_time[at(5)].last_error is None
    assert by_time[at(7)].status is NotificationStatus.SENT  # terminal rows are never revived


async def test_claim_takes_only_due_rows_oldest_first_and_respects_limit(
    uow_factory: UowFactory,
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        for minutes in (30, -3, -1, -2):
            await uow.notifications.add(new_notification(user, reminder, at(minutes)), T0)
    async with uow_factory() as uow:
        claimed = await uow.notifications.claim_due(T0, limit=2, lease=LEASE)
    assert [c.notification.fire_at for c in claimed] == [at(-3), at(-2)]
    assert {c.notification.status for c in claimed} == {NotificationStatus.CLAIMED}
    assert {c.notification.attempts for c in claimed} == {1}
    assert {c.notification.claimed_until for c in claimed} == {T0 + LEASE}
    assert all(c.user.id == user.id for c in claimed)
    assert all(c.reminder is not None and c.reminder.id == reminder.id for c in claimed)
    async with uow_factory() as uow:
        rest = await uow.notifications.claim_due(T0, limit=10, lease=LEASE)
        again = await uow.notifications.claim_due(T0, limit=10, lease=LEASE)
    assert [c.notification.fire_at for c in rest] == [at(-1)]
    assert again == []


async def test_expired_lease_is_reclaimed_and_counts_an_attempt(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, reminder, at(-1)), T0)
    async with uow_factory() as uow:
        first = await uow.notifications.claim_due(T0, 10, LEASE)
    async with uow_factory() as uow:
        too_early = await uow.notifications.claim_due(T0 + LEASE - timedelta(seconds=1), 10, LEASE)
        reclaimed = await uow.notifications.claim_due(T0 + LEASE, 10, LEASE)
    assert len(first) == 1
    assert too_early == []
    assert [c.notification.attempts for c in reclaimed] == [2]


async def test_finish_is_fenced_by_the_attempt_counter(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, reminder, at(-1)), T0)
    async with uow_factory() as uow:
        (stale,) = await uow.notifications.claim_due(T0, 10, LEASE)
    later = T0 + LEASE
    async with uow_factory() as uow:
        (fresh,) = await uow.notifications.claim_due(later, 10, LEASE)  # lease expired: taken over
    async with uow_factory() as uow:
        lost = await uow.notifications.finish(
            stale.notification.id,
            attempts=stale.notification.attempts,
            status=NotificationStatus.SENT,
            now=later,
            message_id=1,
        )
        won = await uow.notifications.finish(
            fresh.notification.id,
            attempts=fresh.notification.attempts,
            status=NotificationStatus.SENT,
            now=later,
            message_id=2,
        )
    assert (lost, won) == (False, True)
    async with uow_factory() as uow:
        stored = await uow.notifications.get(fresh.notification.id)
    assert stored is not None
    assert stored.status is NotificationStatus.SENT
    assert stored.telegram_message_id == 2
    assert stored.sent_at == later
    assert stored.claimed_until is None


async def test_release_returns_to_pending_and_can_refund_the_attempt(
    uow_factory: UowFactory,
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, reminder, at(-1)), T0)
    async with uow_factory() as uow:
        (item,) = await uow.notifications.claim_due(T0, 10, LEASE)
    async with uow_factory() as uow:
        assert await uow.notifications.release(
            item.notification.id,
            attempts=item.notification.attempts,
            fire_at=at(5),
            error="429",
            count_attempt=False,
        )
    async with uow_factory() as uow:
        stored = await uow.notifications.get(item.notification.id)
        not_yet = await uow.notifications.claim_due(at(4), 10, LEASE)
        due = await uow.notifications.claim_due(at(5), 10, LEASE)
    assert stored is not None
    assert (stored.status, stored.attempts, stored.fire_at, stored.last_error) == (
        NotificationStatus.PENDING,
        0,
        at(5),
        "429",
    )
    assert not_yet == []
    assert len(due) == 1
    assert due[0].notification.scheduled_at == at(-1)  # the original schedule never moves


async def test_skip_and_open_queries(uow_factory: UowFactory) -> None:
    alice = await add_user(uow_factory, 1)
    bob = await add_user(uow_factory, 2)
    ra = await add_reminder(uow_factory, alice)
    rb = await add_reminder(uow_factory, bob)
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(alice, ra, at(10)), T0)
        await uow.notifications.add(
            new_notification(alice, ra, at(20), kind=NotificationKind.LEAD), T0
        )
        await uow.notifications.add(new_notification(bob, rb, at(30)), T0)
    async with uow_factory() as uow:
        assert await uow.notifications.has_open_for_reminder(ra.id)
        skipped = await uow.notifications.skip_open_for_user(alice.id, "recipient_blocked")
        assert skipped == 2
        assert not await uow.notifications.has_open_for_reminder(ra.id)
        assert await uow.notifications.has_open_for_reminder(rb.id)
        assert await uow.notifications.skip_open_for_reminder(rb.id, "reminder_deleted") == 1
    async with uow_factory() as uow:
        rows = await uow.notifications.list_for_reminder(ra.id)
    assert {r.status for r in rows} == {NotificationStatus.SKIPPED}
    assert {r.last_error for r in rows} == {"recipient_blocked"}


async def test_earliest_open_fire_at_and_queue_lag(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        assert await uow.notifications.earliest_open_fire_at() is None
        assert await uow.notifications.queue_lag(T0) == timedelta(0)
        await uow.notifications.add(new_notification(user, reminder, at(-7)), T0)
        await uow.notifications.add(new_notification(user, reminder, at(40), seq=1), T0)
    async with uow_factory() as uow:
        assert await uow.notifications.earliest_open_fire_at() == at(-7)
        assert await uow.notifications.queue_lag(T0) == timedelta(minutes=7)
        await uow.notifications.claim_due(T0, 10, LEASE)  # leases the overdue one
    async with uow_factory() as uow:
        # the next interesting moment is the lease expiry (T0 + 60s), not the +40 min row
        assert await uow.notifications.earliest_open_fire_at() == T0 + LEASE
        assert await uow.notifications.queue_lag(T0) == timedelta(0)


async def test_count_sent_since(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        for minutes in (-30, -20, -10):
            await uow.notifications.add(new_notification(user, reminder, at(minutes)), T0)
    async with uow_factory() as uow:
        claimed = await uow.notifications.claim_due(T0, 10, LEASE)
        for i, item in enumerate(claimed):
            await uow.notifications.finish(
                item.notification.id,
                attempts=1,
                status=NotificationStatus.SENT,
                now=at(-30 + 10 * i),
                message_id=100 + i,
            )
    async with uow_factory() as uow:
        assert await uow.notifications.count_sent_since(user.id, at(-25)) == 2
        assert await uow.notifications.count_sent_since(user.id, at(-5)) == 0
        assert await uow.notifications.count_sent_since(user.id + 99, at(-60)) == 0


async def test_recurring_without_open_notification(uow_factory: UowFactory) -> None:
    user = await add_user(uow_factory)
    covered = await add_reminder(uow_factory, user, rrule="FREQ=DAILY")
    orphan = await add_reminder(uow_factory, user, rrule="FREQ=DAILY")
    await add_reminder(uow_factory, user)  # one-off: never reported
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, covered, at(60)), T0)
    async with uow_factory() as uow:
        found = await uow.reminders.recurring_without_open_notification(user.id)
    assert [r.id for r in found] == [orphan.id]


# --- schema guarantees ---


async def test_deleting_a_reminder_cascades_to_its_notifications(
    uow_factory: UowFactory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, reminder, at(5)), T0)
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM reminders"))
    async with session_factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM notifications"))).scalar() == 0


async def test_status_check_constraint_rejects_garbage(
    uow_factory: UowFactory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE reminders SET status = 'bogus' WHERE id = :id"), {"id": reminder.id}
            )


async def test_telegram_message_id_is_unique_per_user_but_nulls_are_free(
    uow_factory: UowFactory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        for minutes in (1, 2, 3):
            await uow.notifications.add(new_notification(user, reminder, at(minutes)), T0)
    async with session_factory() as session, session.begin():
        await session.execute(text("UPDATE notifications SET telegram_message_id = 7 WHERE id = 1"))
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE notifications SET telegram_message_id = 7 WHERE id = 2")
            )


async def test_category_name_must_be_unique_per_user_on_the_folded_key(
    uow_factory: UowFactory, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await add_user(uow_factory)
    insert = text(
        "INSERT INTO categories (user_id, name, name_key, position) VALUES (:u, :n, :k, 0)"
    )
    async with session_factory() as session, session.begin():
        await session.execute(insert, {"u": user.id, "n": "Работа", "k": "работа"})
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(insert, {"u": user.id, "n": "РАБОТА", "k": "работа"})


# --- concurrency ---


async def test_parallel_claimers_never_receive_the_same_row(
    uow_factory: UowFactory, engine: AsyncEngine
) -> None:
    user = await add_user(uow_factory)
    reminder = await add_reminder(uow_factory, user)
    async with uow_factory() as uow:
        for i in range(120):
            await uow.notifications.add(
                new_notification(user, reminder, T0 - timedelta(seconds=i + 1)), T0
            )

    async def claimer() -> list[int]:
        taken: list[int] = []
        while True:
            async with uow_factory() as uow:
                batch = await uow.notifications.claim_due(T0, limit=7, lease=LEASE)
            if not batch:
                return taken
            taken.extend(c.notification.id for c in batch)
            await asyncio.sleep(0)

    results = await asyncio.gather(*(claimer() for _ in range(8)))
    everything = [nid for part in results for nid in part]
    assert len(everything) == len(set(everything)) == 120
    assert sum(1 for part in results if part) > 1  # the work was genuinely shared


def test_clock_helper_is_utc() -> None:
    assert T0.tzinfo is UTC
