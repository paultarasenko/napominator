"""Shared test doubles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeClock:
    """Deterministic ``Clock``. Time only moves when the test says so."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            msg = "FakeClock requires an aware datetime"
            raise ValueError(msg)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


# --- builders ---

from collections.abc import Callable  # noqa: E402

from napominator.domain.entities import (  # noqa: E402
    NewNotification,
    NewReminder,
    NotificationKind,
    Reminder,
    User,
    dedup_key,
)
from napominator.infrastructure.db.uow import SqlAlchemyUnitOfWork  # noqa: E402

UowFactory = Callable[[], SqlAlchemyUnitOfWork]
T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)  # same as FakeClock's default


async def add_user(uow_factory: UowFactory, telegram_id: int = 1001, now: datetime = T0) -> User:
    async with uow_factory() as uow:
        return await uow.users.add(telegram_id, f"user{telegram_id}", f"User {telegram_id}", now)


async def add_reminder(
    uow_factory: UowFactory,
    user: User,
    *,
    text: str = "Позвонить маме",
    dtstart_local: datetime = datetime(2026, 10, 13, 15, 0),
    rrule: str | None = None,
    tz: str = "Europe/Moscow",
    lead_minutes: tuple[int, ...] = (),
    now: datetime = T0,
) -> Reminder:
    async with uow_factory() as uow:
        return await uow.reminders.add(
            NewReminder(
                user_id=user.id,
                text=text,
                tz=tz,
                dtstart_local=dtstart_local,
                rrule=rrule,
                lead_minutes=lead_minutes,
            ),
            now,
        )


def new_notification(
    user: User,
    reminder: Reminder | None,
    fire_at: datetime,
    *,
    kind: NotificationKind = NotificationKind.DUE,
    seq: int = 0,
) -> NewNotification:
    return NewNotification(
        user_id=user.id,
        reminder_id=reminder.id if reminder else None,
        kind=kind,
        occurrence_at=fire_at,
        fire_at=fire_at,
        dedup_key=dedup_key(
            kind,
            fire_at,
            reminder_id=reminder.id if reminder else None,
            user_id=user.id,
            seq=seq,
        ),
        seq=seq,
    )


# --- notifier / sleeper doubles ---

import asyncio  # noqa: E402
from collections import deque  # noqa: E402

from napominator.application.ports import Delivery  # noqa: E402


class Crash(Exception):  # noqa: N818 - stands for "the process died here"
    pass


class FakeNotifier:
    """Scriptable ``Notifier``. Records every message that 'reached Telegram'."""

    def __init__(self, clock: FakeClock, id_start: int = 1000) -> None:
        self._clock = clock
        self._next_id = id_start
        self.sent: list[Delivery] = []
        self.sent_at: list[datetime] = []
        self.calls = 0
        self.outcomes: deque[BaseException | None] = deque()  # consumed one per call
        self.fail_for_chat: dict[int, BaseException] = {}  # persistent, per chat
        self.gate: asyncio.Event | None = None  # block every send until set
        self.crash_after_send = False  # "the process died after Telegram accepted it"
        self.in_flight = asyncio.Event()

    async def send(self, delivery: Delivery) -> int:
        self.calls += 1
        self.in_flight.set()
        if self.gate is not None:
            await self.gate.wait()
        if delivery.chat_id in self.fail_for_chat:
            raise self.fail_for_chat[delivery.chat_id]
        if self.outcomes:
            outcome = self.outcomes.popleft()
            if outcome is not None:
                raise outcome
        self.sent.append(delivery)
        self.sent_at.append(self._clock.now())
        self._next_id += 1
        if self.crash_after_send:
            raise Crash
        return self._next_id


class FakeSleeper:
    """Virtual time: sleeping moves the FakeClock forward to the requested deadline."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        deadline = self._clock.now() + timedelta(seconds=seconds)
        await asyncio.sleep(0)
        if self._clock.now() < deadline:
            self._clock.set(deadline)
