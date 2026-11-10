"""Read models for the list screens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from napominator.application.actions import ACK_RETENTION
from napominator.application.ports import UnitOfWork
from napominator.domain.entities import (
    AwaitingAck,
    ReminderFilter,
    ReminderStatus,
    ReminderView,
    User,
)
from napominator.domain.timeutil import load_zone, local_to_utc, utc_to_local

PAGE_SIZE = 8
_ACK_LIMIT = 20


@dataclass(frozen=True, slots=True)
class Page:
    items: list[ReminderView]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


@dataclass(frozen=True, slots=True)
class TodayView:
    upcoming: list[ReminderView]  # until the end of the user's local day
    awaiting: list[AwaitingAck]  # delivered, not acknowledged yet
    local_date: date


def local_day_bounds(user: User, now: datetime) -> tuple[datetime, datetime]:
    """UTC bounds of the user's current local day."""
    tz = load_zone(user.timezone or "UTC")
    today = utc_to_local(now, tz).date()
    start = local_to_utc(datetime.combine(today, datetime.min.time()), tz)
    end = local_to_utc(datetime.combine(today + timedelta(days=1), datetime.min.time()), tz)
    return start, end


async def today(uow: UnitOfWork, user: User, now: datetime) -> TodayView:
    _, end = local_day_bounds(user, now)
    upcoming = await uow.reminders.search(
        user.id,
        ReminderFilter(next_from=now - timedelta(minutes=1), next_to=end),
        limit=PAGE_SIZE * 3,
    )
    awaiting = await uow.notifications.awaiting_ack(user.id, now - ACK_RETENTION, _ACK_LIMIT)
    tz = load_zone(user.timezone or "UTC")
    return TodayView(upcoming, awaiting, utc_to_local(now, tz).date())


async def page_of(uow: UnitOfWork, user_id: int, flt: ReminderFilter, page: int) -> Page:
    total = await uow.reminders.count(user_id, flt)
    last = max(0, -(-total // PAGE_SIZE) - 1)
    page = min(max(page, 0), last)
    items = await uow.reminders.search(user_id, flt, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    return Page(items, total, page, PAGE_SIZE)


LIST_FILTERS: dict[str, ReminderFilter] = {
    "soon": ReminderFilter(statuses=(ReminderStatus.ACTIVE,)),
    "recurring": ReminderFilter(
        statuses=(ReminderStatus.ACTIVE, ReminderStatus.PAUSED), recurring=True
    ),
    "done": ReminderFilter(statuses=(ReminderStatus.DONE,), order="completed"),
    "all": ReminderFilter(
        statuses=(ReminderStatus.ACTIVE, ReminderStatus.PAUSED, ReminderStatus.DONE)
    ),
}
