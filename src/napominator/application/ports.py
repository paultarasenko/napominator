"""Ports (Protocols) at the application boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, Self

if TYPE_CHECKING:
    from napominator.domain.entities import (
        AdminSnapshot,
        AwaitingAck,
        Category,
        ClaimedNotification,
        NewNotification,
        NewReminder,
        Notification,
        NotificationKind,
        NotificationStatus,
        Reminder,
        ReminderFilter,
        ReminderStatus,
        ReminderView,
        User,
    )
    from napominator.nlp import ParseContext, ParseResult


class Clock(Protocol):
    """The only legal source of "now". Always returns an aware UTC datetime."""

    def now(self) -> datetime: ...


class ReminderParser(Protocol):
    """Turns free text into a structured reminder (rule-based now, optional LLM fallback)."""

    def parse(self, text: str, ctx: ParseContext) -> ParseResult: ...


class UserRepository(Protocol):
    async def get(self, user_id: int) -> User | None: ...

    async def get_by_telegram_id(self, telegram_id: int) -> User | None: ...

    async def add(
        self, telegram_id: int, username: str | None, first_name: str | None, now: datetime
    ) -> User: ...

    async def set_active(self, user_id: int, *, active: bool) -> None: ...

    async def update(self, user_id: int, **changes: Any) -> None:
        """Update whitelisted profile fields (timezone, anchors, digest, names, last_seen...)."""

    async def delete(self, user_id: int) -> bool:
        """Remove the user and, through foreign keys, everything that belongs to them."""


class ReminderRepository(Protocol):
    async def add(self, new: NewReminder, now: datetime) -> Reminder: ...

    async def get(self, reminder_id: int, user_id: int) -> Reminder | None:
        """Owner-checked lookup: a foreign id behaves exactly like a missing one."""

    async def transition(
        self,
        reminder_id: int,
        user_id: int,
        *,
        expected: Sequence[ReminderStatus],
        new: ReminderStatus,
        now: datetime,
    ) -> bool:
        """Conditional status change; ``False`` when the current status is not expected."""

    async def count_active(self, user_id: int) -> int: ...

    async def update(self, reminder_id: int, user_id: int, now: datetime, **changes: Any) -> bool:
        """Update whitelisted fields (text, category_id, tz, dtstart_local, rrule, ...)."""

    async def search(
        self, user_id: int, flt: ReminderFilter, *, limit: int, offset: int = 0
    ) -> list[ReminderView]: ...

    async def count(self, user_id: int, flt: ReminderFilter) -> int: ...

    async def all_for_user(self, user_id: int) -> list[Reminder]: ...

    async def purge(self, *, done_before: datetime, deleted_before: datetime) -> int:
        """Hard-delete finished reminders older than ``done_before`` and soft-deleted ones
        older than ``deleted_before``."""

    async def list_live(self, user_id: int) -> list[Reminder]:
        """Every active or paused reminder (used when the time zone changes)."""

    async def recurring_without_open_notification(self, user_id: int) -> list[Reminder]: ...

    async def close_one_off_without_open_notification(self, user_id: int, now: datetime) -> int:
        """Mark active one-off reminders with nothing left to deliver as done."""


class NotificationRepository(Protocol):
    async def add(self, new: NewNotification, now: datetime) -> bool:
        """Idempotent insert; ``False`` when the key exists (a ``skipped`` row is revived)."""

    async def claim_due(
        self, now: datetime, limit: int, lease: timedelta
    ) -> list[ClaimedNotification]:
        """Atomically lease up to ``limit`` due notifications, oldest first."""

    async def finish(
        self,
        notification_id: int,
        *,
        attempts: int,
        status: NotificationStatus,
        now: datetime,
        error: str | None = None,
        message_id: int | None = None,
    ) -> bool:
        """Fenced transition out of ``claimed``; ``False`` if the lease was lost."""

    async def release(
        self,
        notification_id: int,
        *,
        attempts: int,
        fire_at: datetime,
        error: str | None,
        count_attempt: bool,
    ) -> bool:
        """Fenced return to ``pending`` with a new fire time."""

    async def skip_open_for_user(self, user_id: int, reason: str) -> int: ...

    async def skip_open_for_reminder(self, reminder_id: int, reason: str) -> int: ...

    async def has_open_for_reminder(self, reminder_id: int) -> bool: ...

    async def earliest_open_fire_at(self) -> datetime | None: ...

    async def queue_lag(self, now: datetime) -> timedelta: ...

    async def count_sent_since(self, user_id: int, since: datetime) -> int: ...

    async def get(self, notification_id: int) -> Notification | None: ...

    async def get_for_user(self, notification_id: int, user_id: int) -> Notification | None: ...

    async def get_by_message(
        self, user_id: int, telegram_message_id: int
    ) -> Notification | None: ...

    async def transition(
        self,
        notification_id: int,
        user_id: int,
        *,
        expected: Sequence[NotificationStatus],
        new: NotificationStatus,
        acked_at: datetime | None,
    ) -> bool:
        """Owner-checked, conditional status change that also sets/clears ``acked_at``."""

    async def awaiting_ack(
        self, user_id: int, since: datetime, limit: int
    ) -> list[AwaitingAck]: ...

    async def skip_open(
        self, reminder_id: int, kinds: Sequence[NotificationKind], reason: str
    ) -> int: ...

    async def list_for_reminder(self, reminder_id: int) -> list[Notification]: ...

    async def all_for_user(self, user_id: int) -> list[Notification]: ...

    async def purge_history(self, before: datetime) -> int:
        """Delete settled notifications (not pending/claimed) scheduled before ``before``."""


class CategoryRepository(Protocol):
    async def list(self, user_id: int) -> list[Category]: ...

    async def get(self, category_id: int, user_id: int) -> Category | None: ...

    async def add(self, user_id: int, name: str, emoji: str | None) -> Category | None:
        """``None`` when a category with the same (case-folded) name already exists."""

    async def update(self, category_id: int, user_id: int, **changes: Any) -> bool: ...

    async def delete(self, category_id: int, user_id: int) -> bool: ...

    async def count(self, user_id: int) -> int: ...

    async def count_reminders(self, category_id: int, user_id: int) -> int: ...

    async def move_reminders(self, user_id: int, from_id: int, to_id: int | None) -> int: ...

    async def swap(self, user_id: int, category_id: int, direction: int) -> bool: ...


class HintRepository(Protocol):
    async def bump(self, user_id: int, tokens: Sequence[str], category_id: int) -> None: ...

    async def learned(self, user_id: int, tokens: Sequence[str]) -> Mapping[str, tuple[int, int]]:
        """token -> (category_id, count)."""

    async def all_for_user(self, user_id: int) -> list[tuple[str, int, int]]:
        """(token, category_id, count) for the data export."""


class AdminRepository(Protocol):
    async def snapshot(self, now: datetime) -> AdminSnapshot: ...


class ProcessedUpdateRepository(Protocol):
    async def register(self, update_id: int, now: datetime) -> bool:
        """``True`` the first time an update id is seen, ``False`` for a replay."""

    async def purge_before(self, cutoff: datetime) -> int: ...


class UnitOfWork(Protocol):
    """One transaction. ``async with`` commits on success and rolls back on any exception."""

    @property
    def users(self) -> UserRepository: ...

    @property
    def reminders(self) -> ReminderRepository: ...

    @property
    def notifications(self) -> NotificationRepository: ...

    @property
    def categories(self) -> CategoryRepository: ...

    @property
    def hints(self) -> HintRepository: ...

    @property
    def processed_updates(self) -> ProcessedUpdateRepository: ...

    @property
    def admin(self) -> AdminRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class Delivery:
    """What a Notifier needs to render and send one message."""

    notification_id: int
    kind: NotificationKind
    chat_id: int  # Telegram chat id (== telegram_id for private chats)
    user_id: int
    reminder_id: int | None
    text: str
    occurrence_local: datetime | None  # naive wall-clock time in ``tz``
    tz: str | None
    late_by: timedelta | None  # set when we are noticeably behind schedule
    is_recurring: bool
    seq: int  # nag number, or the lead time in minutes for ``lead`` notifications
    nag_total: int | None = None
    quick: tuple[tuple[str, str], ...] = ()  # (label, snooze code) shown under the message


class Notifier(Protocol):
    async def send(self, delivery: Delivery) -> int:
        """Send and return the Telegram message id.

        Raises ``RetryAfterError``, ``TransientDeliveryError``, ``RecipientBlockedError`` or
        ``PermanentDeliveryError``.
        """
