"""Delivery engine simulations (spec section 7) on SQLite and PostgreSQL."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from napominator.application.dispatcher import Dispatcher, DispatcherConfig
from napominator.application.errors import (
    PermanentDeliveryError,
    RecipientBlockedError,
    RetryAfterError,
    TransientDeliveryError,
)
from napominator.application.reminders import reactivate_user
from napominator.domain.entities import (
    NewReminder,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reminder,
    ReminderStatus,
    User,
)
from tests.support import (
    T0,
    Crash,
    FakeClock,
    FakeNotifier,
    FakeSleeper,
    UowFactory,
    add_reminder,
    add_user,
    new_notification,
)

_TELEGRAM_IDS = itertools.count(1001)
ONE_OFF_LOCAL = datetime(2026, 10, 13, 15, 0)  # 15:00 Moscow == T0
DAILY_0900_LOCAL = datetime(2026, 10, 13, 9, 0)  # 06:00Z every day


@pytest.fixture
def notifier(clock: FakeClock) -> FakeNotifier:
    return FakeNotifier(clock)


@pytest.fixture
def sleeper(clock: FakeClock) -> FakeSleeper:
    return FakeSleeper(clock)


@pytest.fixture
def make(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> Callable[..., Dispatcher]:
    def build(**config: object) -> Dispatcher:
        return Dispatcher(
            uow_factory,
            notifier,
            clock,
            DispatcherConfig(**config),  # type: ignore[arg-type]
            sleep=sleeper,
            rng=lambda: 0.5,  # zero jitter
        )

    return build


async def seed(
    uow_factory: UowFactory,
    *,
    fire_at: datetime = T0,
    rrule: str | None = None,
    dtstart_local: datetime = ONE_OFF_LOCAL,
    user: User | None = None,
    kind: NotificationKind = NotificationKind.DUE,
    text: str = "Позвонить маме",
) -> tuple[User, Reminder, Notification]:
    user = user or await add_user(uow_factory, next(_TELEGRAM_IDS))
    reminder = await add_reminder(
        uow_factory, user, rrule=rrule, dtstart_local=dtstart_local, text=text
    )
    async with uow_factory() as uow:
        await uow.notifications.add(new_notification(user, reminder, fire_at, kind=kind), T0)
        (notification,) = await uow.notifications.list_for_reminder(reminder.id)
    return user, reminder, notification


async def notification(uow_factory: UowFactory, notification_id: int) -> Notification:
    async with uow_factory() as uow:
        found = await uow.notifications.get(notification_id)
    assert found is not None
    return found


async def reminder_of(uow_factory: UowFactory, reminder: Reminder) -> Reminder:
    async with uow_factory() as uow:
        found = await uow.reminders.get(reminder.id, reminder.user_id)
    assert found is not None
    return found


async def all_for(uow_factory: UowFactory, reminder: Reminder) -> list[Notification]:
    async with uow_factory() as uow:
        return await uow.notifications.list_for_reminder(reminder.id)


# --- happy paths ---


async def test_one_off_is_delivered_once_and_closed(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    user, reminder, note = await seed(uow_factory)
    dispatcher = make()
    assert await dispatcher.tick() == 1
    assert await dispatcher.tick() == 0  # nothing left

    (delivery,) = notifier.sent
    assert (delivery.chat_id, delivery.text, delivery.kind) == (
        user.telegram_id,
        "Позвонить маме",
        NotificationKind.DUE,
    )
    assert delivery.occurrence_local == ONE_OFF_LOCAL
    assert delivery.tz == "Europe/Moscow"
    assert delivery.late_by is None
    assert not delivery.is_recurring
    stored = await notification(uow_factory, note.id)
    assert stored.status is NotificationStatus.SENT
    assert stored.telegram_message_id == 1001
    assert stored.sent_at == T0
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.DONE
    assert dispatcher.stats.sent == 1


async def test_nothing_is_sent_before_the_fire_time(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    await seed(uow_factory, fire_at=T0 + timedelta(seconds=1))
    assert await make().tick() == 0
    assert notifier.calls == 0


async def test_recurring_series_queues_exactly_one_next_occurrence(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, _ = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    dispatcher = make()
    await dispatcher.tick()
    await dispatcher.tick()
    rows = await all_for(uow_factory, reminder)
    assert [(r.status, r.fire_at) for r in rows] == [
        (NotificationStatus.SENT, datetime(2026, 10, 13, 6, 0, tzinfo=UTC)),
        (NotificationStatus.PENDING, datetime(2026, 10, 14, 6, 0, tzinfo=UTC)),
    ]
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.ACTIVE
    assert notifier.sent[0].is_recurring


async def test_series_ends_when_count_is_exhausted(
    uow_factory: UowFactory, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, _ = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY;COUNT=1",
        dtstart_local=DAILY_0900_LOCAL,
    )
    await make().tick()
    assert len(await all_for(uow_factory, reminder)) == 1
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.DONE


async def test_lead_notification_does_not_advance_the_series(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    user, reminder, _ = await seed(uow_factory, fire_at=T0 + timedelta(hours=1))
    async with uow_factory() as uow:
        await uow.notifications.add(
            new_notification(user, reminder, T0, kind=NotificationKind.LEAD, seq=60), T0
        )
    await make().tick()
    assert [d.kind for d in notifier.sent] == [NotificationKind.LEAD]
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.ACTIVE


async def test_a_delivered_snooze_closes_a_one_off(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, _ = await seed(uow_factory, kind=NotificationKind.SNOOZE)
    await make().tick()
    assert notifier.sent[0].kind is NotificationKind.SNOOZE
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.DONE
    assert len(await all_for(uow_factory, reminder)) == 1


async def test_a_delivered_snooze_leaves_a_series_untouched(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, _ = await seed(
        uow_factory,
        kind=NotificationKind.SNOOZE,
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    await make().tick()
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.ACTIVE
    assert len(await all_for(uow_factory, reminder)) == 1  # no second occurrence is invented


async def test_digest_has_no_reminder_and_is_just_sent(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    user = await add_user(uow_factory)
    async with uow_factory() as uow:
        await uow.notifications.add(
            new_notification(user, None, T0, kind=NotificationKind.DIGEST), T0
        )
    await make().tick()
    (delivery,) = notifier.sent
    assert (delivery.kind, delivery.reminder_id, delivery.text) == (
        NotificationKind.DIGEST,
        None,
        "",
    )


# --- lateness, expiry, downtime ---


@pytest.mark.parametrize(
    ("late", "expected"),
    [
        (timedelta(seconds=90), None),
        (timedelta(minutes=2), None),
        (timedelta(minutes=2, seconds=1), timedelta(minutes=2, seconds=1)),
        (timedelta(hours=2), timedelta(hours=2)),
    ],
)
async def test_late_delivery_is_marked(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    late: timedelta,
    expected: timedelta | None,
) -> None:
    await seed(uow_factory, fire_at=T0 - late)
    await make().tick()
    assert notifier.sent[0].late_by == expected


async def test_after_downtime_overdue_items_go_out_in_schedule_order(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    for minutes, text in ((50, "третье"), (110, "первое"), (80, "второе")):
        await seed(uow_factory, fire_at=T0 - timedelta(minutes=minutes), text=text)
    await make(max_concurrency=1, chat_rate=1000.0, global_rate=1000.0).tick()
    assert [d.text for d in notifier.sent] == ["первое", "второе", "третье"]


async def test_a_series_does_not_avalanche_after_a_long_outage(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    _, reminder, _ = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    clock.set(datetime(2026, 10, 16, 12, 0, tzinfo=UTC))  # three days down
    dispatcher = make()
    await dispatcher.tick()
    await dispatcher.tick()
    rows = await all_for(uow_factory, reminder)
    assert [(r.status, r.fire_at.day) for r in rows] == [
        (NotificationStatus.EXPIRED, 13),
        (NotificationStatus.PENDING, 17),
    ]
    assert notifier.calls == 0
    assert dispatcher.stats.expired == 1


async def test_a_two_hour_outage_still_delivers_with_a_late_marker(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    _, reminder, _ = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    clock.set(datetime(2026, 10, 13, 8, 0, tzinfo=UTC))
    await make().tick()
    assert notifier.sent[0].late_by == timedelta(hours=2)
    assert len(await all_for(uow_factory, reminder)) == 2


# --- failures ---


async def test_retry_after_waits_without_counting_an_attempt(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.outcomes.extend([RetryAfterError(7)])
    dispatcher = make()
    await dispatcher.tick()
    stored = await notification(uow_factory, note.id)
    assert (stored.status, stored.attempts, stored.fire_at) == (
        NotificationStatus.PENDING,
        0,
        T0 + timedelta(seconds=7),
    )
    assert await dispatcher.tick() == 0
    clock.advance(seconds=7)
    assert await dispatcher.tick() == 1
    assert (await notification(uow_factory, note.id)).status is NotificationStatus.SENT
    assert dispatcher.stats.rate_limited == 1


async def test_transient_errors_back_off_exponentially_then_succeed(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.outcomes.extend([TransientDeliveryError("net")] * 3)
    dispatcher = make()
    observed: list[float] = []
    for _ in range(3):
        await dispatcher.tick()
        stored = await notification(uow_factory, note.id)
        delay = (stored.fire_at - clock.now()).total_seconds()
        observed.append(delay)
        assert stored.status is NotificationStatus.PENDING
        clock.advance(seconds=delay)
    assert observed == [2.0, 4.0, 8.0]
    await dispatcher.tick()
    final = await notification(uow_factory, note.id)
    assert (final.status, final.attempts) == (NotificationStatus.SENT, 4)
    assert dispatcher.stats.retried == 3


async def test_backoff_is_capped_and_jittered(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    clock: FakeClock,
    sleeper: FakeSleeper,
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.outcomes.extend([TransientDeliveryError("net")] * 3)
    dispatcher = Dispatcher(
        uow_factory,
        notifier,
        clock,
        DispatcherConfig(backoff_base=100.0, backoff_cap=150.0, backoff_jitter=0.2),
        sleep=sleeper,
        rng=lambda: 1.0,  # maximum jitter: +20 %
    )
    delays: list[float] = []
    for _ in range(3):
        await dispatcher.tick()
        stored = await notification(uow_factory, note.id)
        delays.append((stored.fire_at - clock.now()).total_seconds())
        clock.set(stored.fire_at)
    # 100 -> 200 -> 400 would be uncapped; the cap clamps the base at 150, jitter adds 20 %
    assert delays == [pytest.approx(120.0), pytest.approx(180.0), pytest.approx(180.0)]


async def test_a_notification_that_never_gets_through_expires_after_24_hours(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    user, reminder, note = await seed(uow_factory)
    notifier.fail_for_chat[user.telegram_id] = TransientDeliveryError("down")
    dispatcher = make(backoff_cap=6 * 3600.0)
    for _ in range(60):
        await dispatcher.tick()
        stored = await notification(uow_factory, note.id)
        if stored.status is not NotificationStatus.PENDING:
            break
        clock.set(max(clock.now(), stored.fire_at))
    assert stored.status is NotificationStatus.EXPIRED
    assert 5 < stored.attempts < 30  # backoff, not a hammering loop
    assert notifier.sent == []
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.DONE


async def test_a_hanging_send_is_cut_off_and_retried(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.gate = asyncio.Event()  # never opened: the network call hangs
    await make(send_timeout=0.05).tick()
    stored = await notification(uow_factory, note.id)
    assert (stored.status, stored.attempts) == (NotificationStatus.PENDING, 1)
    assert stored.last_error is not None
    assert "TimeoutError" in stored.last_error


async def test_permanent_failure_marks_failed_but_the_series_continues(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, note = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    notifier.outcomes.append(PermanentDeliveryError("chat not found"))
    dispatcher = make()
    await dispatcher.tick()
    stored = await notification(uow_factory, note.id)
    assert stored.status is NotificationStatus.FAILED
    assert stored.last_error is not None
    assert "chat not found" in stored.last_error
    assert [r.status for r in await all_for(uow_factory, reminder)] == [
        NotificationStatus.FAILED,
        NotificationStatus.PENDING,
    ]
    assert dispatcher.stats.failed == 1


async def test_blocked_user_is_deactivated_and_their_queue_is_skipped(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    alice, one_off, first = await seed(uow_factory)
    _, later_one_off, later = await seed(uow_factory, user=alice, fire_at=T0 + timedelta(hours=1))
    _, series, series_note = await seed(
        uow_factory,
        user=alice,
        fire_at=datetime(2026, 10, 14, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=datetime(2026, 10, 14, 9, 0),
    )
    bob, bob_reminder, bob_note = await seed(uow_factory, user=await add_user(uow_factory, 2002))
    notifier.fail_for_chat[alice.telegram_id] = RecipientBlockedError("Forbidden: bot was blocked")

    await make().tick()

    async with uow_factory() as uow:
        refreshed = await uow.users.get(alice.id)
        bob_after = await uow.users.get(bob.id)
    assert refreshed is not None
    assert bob_after is not None
    assert refreshed.is_active is False
    assert bob_after.is_active is True
    for nid in (first.id, later.id, series_note.id):
        stored = await notification(uow_factory, nid)
        assert stored.status is NotificationStatus.SKIPPED
        assert stored.last_error == "recipient_blocked"
    assert (await notification(uow_factory, bob_note.id)).status is NotificationStatus.SENT
    assert (await reminder_of(uow_factory, one_off)).status is ReminderStatus.DONE
    assert (await reminder_of(uow_factory, later_one_off)).status is ReminderStatus.DONE
    assert (await reminder_of(uow_factory, series)).status is ReminderStatus.ACTIVE
    assert (await reminder_of(uow_factory, bob_reminder)).status is ReminderStatus.DONE

    # the user comes back with /start: the repeating series is resumed, exactly once
    clock.advance(hours=3)
    async with uow_factory() as uow:
        assert await reactivate_user(uow, alice.id, clock.now()) == 1
    async with uow_factory() as uow:
        assert await reactivate_user(uow, alice.id, clock.now()) == 0
        restored = await uow.users.get(alice.id)
    assert restored is not None
    assert restored.is_active
    pending = [
        r for r in await all_for(uow_factory, series) if r.status is NotificationStatus.PENDING
    ]
    assert [r.fire_at for r in pending] == [datetime(2026, 10, 14, 6, 0, tzinfo=UTC)]


@pytest.mark.parametrize("status", [ReminderStatus.DELETED, ReminderStatus.PAUSED])
async def test_inactive_reminders_are_skipped_not_sent(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    status: ReminderStatus,
) -> None:
    user, reminder, note = await seed(uow_factory)
    async with uow_factory() as uow:
        assert await uow.reminders.transition(
            reminder.id, user.id, expected=[ReminderStatus.ACTIVE], new=status, now=T0
        )
    await make().tick()
    stored = await notification(uow_factory, note.id)
    assert (stored.status, stored.last_error) == (NotificationStatus.SKIPPED, "reminder_inactive")
    assert notifier.calls == 0


async def test_a_corrupt_rrule_closes_the_reminder_instead_of_looping(
    uow_factory: UowFactory, notifier: FakeNotifier, make: Callable[..., Dispatcher]
) -> None:
    _, reminder, note = await seed(uow_factory, rrule="FREQ=BOGUS")
    await make().tick()
    assert (await notification(uow_factory, note.id)).status is NotificationStatus.SENT
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.DONE
    assert len(await all_for(uow_factory, reminder)) == 1


# --- crashes and concurrent dispatchers ---


async def test_crash_between_send_and_commit_redelivers_after_the_lease(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    _, reminder, note = await seed(uow_factory)
    notifier.crash_after_send = True
    dispatcher = make()
    await dispatcher.tick()
    stuck = await notification(uow_factory, note.id)
    assert (stuck.status, stuck.attempts) == (NotificationStatus.CLAIMED, 1)
    assert dispatcher.stats.errors == 1
    assert (await reminder_of(uow_factory, reminder)).status is ReminderStatus.ACTIVE

    notifier.crash_after_send = False
    assert await dispatcher.tick() == 0  # lease still held: nobody touches it yet
    clock.advance(seconds=61)
    assert await dispatcher.tick() == 1
    final = await notification(uow_factory, note.id)
    assert (final.status, final.attempts) == (NotificationStatus.SENT, 2)
    # at-least-once: the user may see it twice, but never zero times (ADR-0003)
    assert len(notifier.sent) == 2
    assert {d.notification_id for d in notifier.sent} == {note.id}


async def test_a_slow_dispatcher_that_lost_its_lease_cannot_clobber_the_new_owner(
    uow_factory: UowFactory,
    clock: FakeClock,
    sleeper: FakeSleeper,
) -> None:
    _, reminder, note = await seed(
        uow_factory,
        fire_at=datetime(2026, 10, 13, 6, 0, tzinfo=UTC),
        rrule="FREQ=DAILY",
        dtstart_local=DAILY_0900_LOCAL,
    )
    slow_notifier = FakeNotifier(clock, id_start=100)
    slow_notifier.gate = asyncio.Event()
    fast_notifier = FakeNotifier(clock, id_start=900)
    slow = Dispatcher(uow_factory, slow_notifier, clock, sleep=sleeper, rng=lambda: 0.5)
    fast = Dispatcher(uow_factory, fast_notifier, clock, sleep=sleeper, rng=lambda: 0.5)

    slow_tick = asyncio.create_task(slow.tick())
    await slow_notifier.in_flight.wait()  # slow has claimed and is "sending"
    clock.advance(seconds=61)  # its lease expires
    await fast.tick()  # the other instance takes over and finishes the job
    slow_notifier.gate.set()
    await slow_tick

    assert slow.stats.lease_lost == 1
    assert slow.stats.sent == 0
    final = await notification(uow_factory, note.id)
    assert (final.status, final.attempts, final.telegram_message_id) == (
        NotificationStatus.SENT,
        2,
        901,
    )
    rows = await all_for(uow_factory, reminder)
    assert [r.status for r in rows] == [NotificationStatus.SENT, NotificationStatus.PENDING]


async def test_two_dispatchers_deliver_every_notification_exactly_once(
    uow_factory: UowFactory, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    total = 120
    async with uow_factory() as uow:
        for i in range(total):
            user = await uow.users.add(10_000 + i, None, None, T0)
            reminder = await uow.reminders.add(
                NewReminder(user.id, f"r{i}", "Europe/Moscow", ONE_OFF_LOCAL), T0
            )
            await uow.notifications.add(new_notification(user, reminder, T0), T0)
    notifiers = [FakeNotifier(clock, id_start=1_000_000), FakeNotifier(clock, id_start=2_000_000)]
    config = DispatcherConfig(batch_size=7, global_rate=10_000.0, chat_rate=10_000.0)
    dispatchers = [
        Dispatcher(uow_factory, n, clock, config, sleep=sleeper, rng=lambda: 0.5) for n in notifiers
    ]

    async def drain(d: Dispatcher) -> None:
        while await d.tick():  # noqa: ASYNC110 - yield so the other dispatcher interleaves
            await asyncio.sleep(0)

    await asyncio.gather(*(drain(d) for d in dispatchers))
    delivered = [d.notification_id for n in notifiers for d in n.sent]
    assert len(delivered) == len(set(delivered)) == total
    assert all(len(n.sent) > 0 for n in notifiers)  # both instances really worked
    async with uow_factory() as uow:
        assert await uow.notifications.earliest_open_fire_at() is None


# --- rate limits ---


async def test_one_chat_is_limited_to_one_message_per_second(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
) -> None:
    user = await add_user(uow_factory)
    for _ in range(6):
        await seed(uow_factory, user=user)
    await make(max_concurrency=1).tick()
    gaps = [
        (b - a).total_seconds()
        for a, b in zip(notifier.sent_at, notifier.sent_at[1:], strict=False)
    ]
    assert len(notifier.sent) == 6
    assert gaps == [pytest.approx(1.0)] * 5


async def test_the_global_limit_applies_across_chats(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
) -> None:
    for i in range(40):
        await seed(uow_factory, user=await add_user(uow_factory, 5000 + i))
    await make(max_concurrency=1, batch_size=40, global_rate=10.0).tick()
    spread = (notifier.sent_at[-1] - notifier.sent_at[0]).total_seconds()
    assert len(notifier.sent) == 40
    assert spread == pytest.approx(3.0, abs=0.01)  # burst of 10, then 30 more at 10/s


async def test_a_users_hourly_cap_defers_without_losing_anything(
    uow_factory: UowFactory,
    notifier: FakeNotifier,
    make: Callable[..., Dispatcher],
    clock: FakeClock,
) -> None:
    user = await add_user(uow_factory)
    notes = [(await seed(uow_factory, user=user))[2] for _ in range(4)]
    dispatcher = make(max_concurrency=1, user_hourly_limit=2, chat_rate=1000.0)
    await dispatcher.tick()
    assert len(notifier.sent) == 2
    deferred = [await notification(uow_factory, n.id) for n in notes]
    waiting = [d for d in deferred if d.status is NotificationStatus.PENDING]
    assert len(waiting) == 2
    assert all(d.attempts == 0 for d in waiting)  # deferral is not a failed attempt
    assert dispatcher.stats.rate_limited == 2
    clock.advance(hours=1, seconds=61)
    await dispatcher.tick()
    assert len(notifier.sent) == 4


# --- observability ---


async def test_queue_lag_is_reported(
    uow_factory: UowFactory, make: Callable[..., Dispatcher]
) -> None:
    await seed(uow_factory, fire_at=T0 - timedelta(minutes=7))
    dispatcher = make()
    await dispatcher.tick()
    assert dispatcher.stats.last_lag_seconds == 420.0


# --- the long-running loop ---


async def eventually(condition: Callable[[], bool], within: float = 5.0) -> None:
    async with asyncio.timeout(within):
        while not condition():  # noqa: ASYNC110 - polling a condition is the point
            await asyncio.sleep(0.01)


async def test_run_delivers_beats_and_stops_on_request(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    await seed(uow_factory)
    beats: list[int] = []
    dispatcher = Dispatcher(
        uow_factory,
        notifier,
        clock,
        DispatcherConfig(poll_seconds=0.02),
        sleep=sleeper,
        heartbeat=lambda: beats.append(1),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run(stop))
    await eventually(lambda: len(notifier.sent) == 1 and len(beats) >= 2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert len(notifier.sent) == 1


async def test_wake_interrupts_a_long_idle_wait(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    dispatcher = Dispatcher(
        uow_factory, notifier, clock, DispatcherConfig(poll_seconds=30.0), sleep=sleeper
    )
    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run(stop))
    await asyncio.sleep(0.1)  # now idling for up to 30 s
    await seed(uow_factory)
    dispatcher.wake()
    await eventually(lambda: len(notifier.sent) == 1, within=3.0)
    stop.set()
    await asyncio.wait_for(task, 5)


class FlakyFactory:
    """Fails the first ``failures`` calls like a database that is down, then recovers."""

    def __init__(self, inner: UowFactory, failures: int) -> None:
        self._inner = inner
        self.failures = failures
        self.calls = 0

    def __call__(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("database is down")
        return self._inner()


async def test_run_survives_a_database_outage_and_only_beats_when_healthy(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    await seed(uow_factory)
    flaky = FlakyFactory(uow_factory, failures=3)
    beats: list[int] = []
    dispatcher = Dispatcher(
        flaky,
        notifier,
        clock,
        DispatcherConfig(poll_seconds=0.02, failure_backoff_base=0.01),
        sleep=sleeper,
        heartbeat=lambda: beats.append(flaky.calls),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run(stop))
    await eventually(lambda: len(notifier.sent) == 1 and bool(beats))
    stop.set()
    await asyncio.wait_for(task, 5)
    assert beats[0] > 3  # no heartbeat while the database was unreachable


async def test_graceful_shutdown_waits_for_the_message_in_flight(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.gate = asyncio.Event()
    dispatcher = Dispatcher(
        uow_factory, notifier, clock, DispatcherConfig(poll_seconds=0.02), sleep=sleeper
    )
    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run(stop))
    await asyncio.wait_for(notifier.in_flight.wait(), 5)
    stop.set()
    await asyncio.sleep(0.1)
    assert not task.done()  # still waiting for the send to finish
    notifier.gate.set()
    await asyncio.wait_for(task, 5)
    assert (await notification(uow_factory, note.id)).status is NotificationStatus.SENT


async def test_shutdown_gives_up_after_the_timeout_and_leaves_the_lease_to_expire(
    uow_factory: UowFactory, notifier: FakeNotifier, clock: FakeClock, sleeper: FakeSleeper
) -> None:
    _, _, note = await seed(uow_factory)
    notifier.gate = asyncio.Event()  # the send never completes
    dispatcher = Dispatcher(
        uow_factory,
        notifier,
        clock,
        DispatcherConfig(poll_seconds=0.02, shutdown_timeout=0.05),
        sleep=sleeper,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(dispatcher.run(stop))
    await asyncio.wait_for(notifier.in_flight.wait(), 5)
    stop.set()
    await asyncio.wait_for(task, 5)
    stuck = await notification(uow_factory, note.id)
    assert stuck.status is NotificationStatus.CLAIMED
    clock.advance(seconds=61)
    notifier.gate.set()
    fresh = Dispatcher(uow_factory, notifier, clock, sleep=sleeper)
    assert await fresh.tick() == 1  # picked up again once the lease ran out


def test_crash_marker_is_an_exception_subclass() -> None:
    assert issubclass(Crash, Exception)
