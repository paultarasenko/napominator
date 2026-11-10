"""Persistent entities as plain, immutable values (no ORM, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum

from napominator.domain.recurrence import Recurrence, Schedule
from napominator.domain.timeutil import load_zone

MAX_TEXT_LENGTH = 1000
MAX_ACTIVE_REMINDERS = 1000
MAX_CATEGORIES = 30
MAX_CATEGORY_NAME = 32


class ReminderStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    DELETED = "deleted"


class NotificationKind(StrEnum):
    DUE = "due"  # the reminder itself
    LEAD = "lead"  # "заранее"
    NAG = "nag"  # persistent repeat until acknowledged
    SNOOZE = "snooze"  # postponed by the user
    DIGEST = "digest"  # morning summary (no reminder)


class NotificationStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SENT = "sent"
    DONE = "done"
    SNOOZED = "snoozed"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXPIRED = "expired"


OPEN_STATUSES = (NotificationStatus.PENDING, NotificationStatus.CLAIMED)


@dataclass(frozen=True, slots=True)
class User:
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    timezone: str | None
    morning_time: time
    midday_time: time
    evening_time: time
    digest_enabled: bool
    digest_time: time
    is_active: bool
    onboarded_at: datetime | None
    created_at: datetime
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    user_id: int
    name: str
    emoji: str | None
    position: int


@dataclass(frozen=True, slots=True)
class ReminderView:
    """A reminder plus the next moment it will fire (``None`` if nothing is queued)."""

    reminder: Reminder
    next_at: datetime | None  # aware UTC


@dataclass(frozen=True, slots=True)
class ReminderFilter:
    """Query description for list screens."""

    statuses: tuple[ReminderStatus, ...] = (ReminderStatus.ACTIVE,)
    category_id: int | None = None
    uncategorized: bool = False
    recurring: bool | None = None
    next_from: datetime | None = None  # aware UTC, inclusive
    next_to: datetime | None = None  # aware UTC, exclusive
    order: str = "next"  # next | created | completed


@dataclass(frozen=True, slots=True)
class AdminSnapshot:
    """Operational numbers for ``/admin``. Contains no user content."""

    users_total: int
    users_active: int
    reminders_active: int
    notifications_open: int
    queue_lag_seconds: float
    failures_24h: int
    recent_errors: tuple[tuple[str, str, str], ...]  # (kind, status, error) of the latest failures


@dataclass(frozen=True, slots=True)
class AwaitingAck:
    """A delivered one-off (or snoozed) notification the user has not reacted to yet."""

    notification: Notification
    reminder: Reminder


@dataclass(frozen=True, slots=True)
class NewReminder:
    user_id: int
    text: str
    tz: str
    dtstart_local: datetime
    rrule: str | None = None
    category_id: int | None = None
    lead_minutes: tuple[int, ...] = ()
    nag_interval_min: int | None = None
    nag_max: int | None = None
    source_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    user_id: int
    category_id: int | None
    text: str
    tz: str
    dtstart_local: datetime
    rrule: str | None
    lead_minutes: tuple[int, ...]
    nag_interval_min: int | None
    nag_max: int | None
    status: ReminderStatus
    deleted_at: datetime | None
    completed_at: datetime | None
    source_message_id: int | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_recurring(self) -> bool:
        return self.rrule is not None

    @property
    def schedule(self) -> Schedule:
        recurrence = Recurrence.from_rrule(self.rrule) if self.rrule else None
        return Schedule(self.dtstart_local, load_zone(self.tz), recurrence)


@dataclass(frozen=True, slots=True)
class NewNotification:
    user_id: int
    reminder_id: int | None
    kind: NotificationKind
    occurrence_at: datetime  # UTC: which occurrence this belongs to
    fire_at: datetime  # UTC
    dedup_key: str
    seq: int = 0


@dataclass(frozen=True, slots=True)
class Notification:
    id: int
    user_id: int
    reminder_id: int | None
    kind: NotificationKind
    occurrence_at: datetime
    scheduled_at: datetime  # original fire time; never changes (used for lateness and expiry)
    fire_at: datetime  # effective fire time (moves on retries)
    status: NotificationStatus
    claimed_until: datetime | None
    attempts: int
    seq: int
    last_error: str | None
    sent_at: datetime | None
    acked_at: datetime | None
    telegram_message_id: int | None
    dedup_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedNotification:
    """A notification this process has leased, with everything needed to deliver it."""

    notification: Notification
    user: User
    reminder: Reminder | None  # None for digests


def dedup_key(
    kind: NotificationKind,
    occurrence_at: datetime,
    *,
    reminder_id: int | None,
    user_id: int,
    seq: int = 0,
) -> str:
    """Idempotency key: one row per (reminder, kind, occurrence, sequence number)."""
    owner = f"r{reminder_id}" if reminder_id is not None else f"u{user_id}"
    return f"{owner}:{kind.value}:{occurrence_at.strftime('%Y%m%dT%H%M%SZ')}:{seq}"
