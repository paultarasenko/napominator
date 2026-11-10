"""Delivery engine: claim -> send -> commit (transactional outbox, ADR-0002/0003).

* The database is the only source of truth; any number of dispatchers may run.
* Sending never happens inside a transaction; claim and result commits are short.
* At-least-once: a crash between "sent" and "committed" re-sends after the lease expires.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from napominator.application.errors import (
    InvalidScheduleError,
    PermanentDeliveryError,
    RecipientBlockedError,
    RetryAfterError,
    TransientDeliveryError,
)
from napominator.application.ports import (
    Clock,
    Delivery,
    Notifier,
    UnitOfWork,
    UnitOfWorkFactory,
)
from napominator.application.ratelimit import RateLimiter
from napominator.application.scheduling import schedule_next
from napominator.domain.entities import (
    ClaimedNotification,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reminder,
    ReminderStatus,
)
from napominator.domain.timeutil import load_zone, utc_to_local
from napominator.infrastructure.logging import get_logger

log = get_logger("dispatcher")

_HOUR = timedelta(hours=1)
_MAX_ERROR = 200


@dataclass(frozen=True, slots=True)
class DispatcherConfig:
    batch_size: int = 20
    lease: timedelta = timedelta(seconds=60)
    poll_seconds: float = 5.0
    max_concurrency: int = 10
    global_rate: float = 25.0  # messages per second, all chats
    chat_rate: float = 1.0  # messages per second, one chat
    send_timeout: float = 30.0  # must stay well below ``lease``
    late_grace: timedelta = timedelta(minutes=2)
    max_age: timedelta = timedelta(hours=24)
    backoff_base: float = 2.0
    backoff_cap: float = 300.0
    backoff_jitter: float = 0.2
    user_hourly_limit: int = 60
    hourly_limit_delay: float = 60.0
    shutdown_timeout: float = 30.0
    failure_backoff_base: float = 2.0  # the loop itself failed (e.g. database down)
    failure_backoff_cap: float = 30.0


@dataclass(slots=True)
class DispatcherStats:
    sent: int = 0
    failed: int = 0
    expired: int = 0
    skipped: int = 0
    retried: int = 0
    rate_limited: int = 0
    lease_lost: int = 0
    errors: int = 0
    last_lag_seconds: float = 0.0


class Dispatcher:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        notifier: Notifier,
        clock: Clock,
        config: DispatcherConfig | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self._uow = uow_factory
        self._notifier = notifier
        self._clock = clock
        self._cfg = config or DispatcherConfig()
        self._sleep = sleep
        self._rng = rng
        self._heartbeat = heartbeat
        self._limiter = RateLimiter(clock, self._cfg.global_rate, self._cfg.chat_rate)
        self._wake = asyncio.Event()
        self.stats = DispatcherStats()

    # --- public API ---

    def wake(self) -> None:
        """Call after creating an earlier notification so it is not delayed by the poll."""
        self._wake.set()

    async def tick(self) -> int:
        """Claim one batch and deliver it. Returns how many notifications were claimed."""
        now = self._clock.now()
        async with self._uow() as uow:
            lag = await uow.notifications.queue_lag(now)
            claimed = await uow.notifications.claim_due(now, self._cfg.batch_size, self._cfg.lease)
        self.stats.last_lag_seconds = lag.total_seconds()
        if not claimed:
            return 0
        gate = asyncio.Semaphore(self._cfg.max_concurrency)
        await asyncio.gather(*(self._guarded(item, gate) for item in claimed))
        return len(claimed)

    async def run(self, stop: asyncio.Event) -> None:
        """Main loop. Returns after ``stop`` is set and in-flight sends have finished."""
        failures = 0
        while not stop.is_set():
            try:
                processed = await self._run_tick(stop)
                pause = 0.0 if processed >= self._cfg.batch_size else await self._idle_seconds()
                failures = 0
                if self._heartbeat is not None:
                    self._heartbeat()  # only after a successful DB round trip
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the database may be down: back off, never crash
                failures += 1
                log.error(
                    "dispatcher_iteration_failed", error=type(exc).__name__, failures=failures
                )
                pause = min(
                    self._cfg.failure_backoff_cap,
                    self._cfg.failure_backoff_base * float(2 ** min(failures - 1, 16)),
                )
            if pause > 0:
                await self._wait(stop, pause)

    # --- loop helpers ---

    async def _run_tick(self, stop: asyncio.Event) -> int:
        tick = asyncio.create_task(self.tick())
        stopper = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({tick, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if not tick.done():  # shutdown requested mid-batch: let in-flight sends finish
                try:
                    await asyncio.wait_for(asyncio.shield(tick), self._cfg.shutdown_timeout)
                except TimeoutError:
                    log.warning("shutdown_timeout_leases_will_expire")
                    tick.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await tick
                    return 0
            return await tick
        finally:
            stopper.cancel()

    async def _idle_seconds(self) -> float:
        async with self._uow() as uow:
            earliest = await uow.notifications.earliest_open_fire_at()
        if earliest is None:
            return self._cfg.poll_seconds
        wait = (earliest - self._clock.now()).total_seconds()
        return min(max(wait, 0.0), self._cfg.poll_seconds)

    async def _wait(self, stop: asyncio.Event, seconds: float) -> None:
        waiters = {asyncio.ensure_future(self._wake.wait()), asyncio.ensure_future(stop.wait())}
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            self._wake.clear()

    # --- delivery of one notification ---

    async def _guarded(self, item: ClaimedNotification, gate: asyncio.Semaphore) -> None:
        async with gate:
            try:
                await self._deliver(item)
            except Exception as exc:  # leave the row leased: it is retried when the lease ends
                self.stats.errors += 1
                log.error(
                    "delivery_crashed",
                    notification_id=item.notification.id,
                    error=type(exc).__name__,
                )

    async def _deliver(self, item: ClaimedNotification) -> None:
        n = item.notification
        now = self._clock.now()
        if n.kind is not NotificationKind.DIGEST and (
            item.reminder is None or item.reminder.status is not ReminderStatus.ACTIVE
        ):
            await self._complete(item, NotificationStatus.SKIPPED, error="reminder_inactive")
            self.stats.skipped += 1
            return
        age = now - n.scheduled_at
        if age > self._cfg.max_age:
            await self._complete(item, NotificationStatus.EXPIRED, error="too_old", advance=True)
            self.stats.expired += 1
            return
        if await self._over_hourly_limit(item):
            await self._release(item, self._cfg.hourly_limit_delay, "hourly_limit", refund=True)
            self.stats.rate_limited += 1
            return

        wait = self._limiter.reserve(item.user.telegram_id)
        if wait > 0:
            await self._sleep(wait)
        try:
            async with asyncio.timeout(self._cfg.send_timeout):
                message_id = await self._notifier.send(self._delivery(item, age))
        except RetryAfterError as exc:  # Telegram asked us to wait: not our failure
            await self._release(item, exc.seconds, "retry_after", refund=True)
            self.stats.rate_limited += 1
        except (TransientDeliveryError, TimeoutError) as exc:
            delay = self._backoff(n.attempts)
            await self._release(item, delay, _reason(exc), refund=False)
            self.stats.retried += 1
        except RecipientBlockedError:
            await self._on_blocked(item)
            self.stats.skipped += 1
        except PermanentDeliveryError as exc:
            await self._complete(item, NotificationStatus.FAILED, error=_reason(exc), advance=True)
            self.stats.failed += 1
        else:
            await self._complete(item, NotificationStatus.SENT, message_id=message_id, advance=True)

    def _delivery(self, item: ClaimedNotification, age: timedelta) -> Delivery:
        n, reminder = item.notification, item.reminder
        local = (
            utc_to_local(n.occurrence_at, load_zone(reminder.tz)) if reminder is not None else None
        )
        return Delivery(
            notification_id=n.id,
            kind=n.kind,
            chat_id=item.user.telegram_id,
            user_id=item.user.id,
            reminder_id=n.reminder_id,
            text=reminder.text if reminder is not None else "",
            occurrence_local=local,
            tz=reminder.tz if reminder is not None else None,
            late_by=age if age > self._cfg.late_grace else None,
            is_recurring=reminder.is_recurring if reminder is not None else False,
            seq=n.seq,
        )

    async def _over_hourly_limit(self, item: ClaimedNotification) -> bool:
        async with self._uow() as uow:
            sent = await uow.notifications.count_sent_since(item.user.id, self._clock.now() - _HOUR)
        return sent >= self._cfg.user_hourly_limit

    def _backoff(self, attempts: int) -> float:
        base = min(self._cfg.backoff_cap, self._cfg.backoff_base * float(2 ** max(attempts - 1, 0)))
        jitter = 1 + self._cfg.backoff_jitter * (2 * self._rng() - 1)
        return base * jitter

    # --- committing outcomes ---

    async def _release(
        self, item: ClaimedNotification, delay: float, reason: str, *, refund: bool
    ) -> None:
        n = item.notification
        async with self._uow() as uow:
            ok = await uow.notifications.release(
                n.id,
                attempts=n.attempts,
                fire_at=self._clock.now() + timedelta(seconds=delay),
                error=reason,
                count_attempt=not refund,
            )
        if not ok:
            self.stats.lease_lost += 1

    async def _complete(
        self,
        item: ClaimedNotification,
        status: NotificationStatus,
        *,
        error: str | None = None,
        message_id: int | None = None,
        advance: bool = False,
    ) -> None:
        """Record the outcome and, in the same transaction, schedule what comes next."""
        n = item.notification
        now = self._clock.now()
        async with self._uow() as uow:
            ok = await uow.notifications.finish(
                n.id,
                attempts=n.attempts,
                status=status,
                now=now,
                error=error,
                message_id=message_id,
            )
            if not ok:
                self.stats.lease_lost += 1
                log.warning("lease_lost", notification_id=n.id)
                return
            if status is NotificationStatus.SENT:
                self.stats.sent += 1
            if (
                advance
                and n.kind in (NotificationKind.DUE, NotificationKind.SNOOZE)
                and item.reminder is not None
            ):
                await self._advance_series(uow, item.reminder, n)

    async def _advance_series(self, uow: UnitOfWork, reminder: Reminder, n: Notification) -> None:
        """After the due notification is settled: queue the next occurrence or close it."""
        now = self._clock.now()
        next_at = None
        if n.kind is NotificationKind.SNOOZE and reminder.is_recurring:
            return  # the series already has its next occurrence queued
        if reminder.is_recurring:
            try:
                next_at = await schedule_next(
                    uow, reminder, after=max(n.occurrence_at, now), now=now
                )
            except InvalidScheduleError:
                log.error("corrupt_schedule_closing_reminder", reminder_id=reminder.id)
        if next_at is None:
            await uow.reminders.transition(
                reminder.id,
                reminder.user_id,
                expected=[ReminderStatus.ACTIVE],
                new=ReminderStatus.DONE,
                now=now,
            )

    async def _on_blocked(self, item: ClaimedNotification) -> None:
        n, now = item.notification, self._clock.now()
        async with self._uow() as uow:
            ok = await uow.notifications.finish(
                n.id,
                attempts=n.attempts,
                status=NotificationStatus.SKIPPED,
                now=now,
                error="recipient_blocked",
            )
            if not ok:
                self.stats.lease_lost += 1
                return
            await uow.users.set_active(item.user.id, active=False)
            await uow.notifications.skip_open_for_user(item.user.id, "recipient_blocked")
            await uow.reminders.close_one_off_without_open_notification(item.user.id, now)
        log.info("recipient_blocked", user_id=item.user.id)


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR]
