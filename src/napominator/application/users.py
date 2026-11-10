"""User registration, onboarding and profile settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from napominator.application.categories import create_default_categories
from napominator.application.errors import InvalidScheduleError
from napominator.application.ports import UnitOfWork
from napominator.application.reminders import reactivate_user
from napominator.domain.entities import User
from napominator.domain.timeutil import is_valid_timezone


@dataclass(frozen=True, slots=True)
class Registration:
    user: User
    created: bool
    returned: bool  # had blocked the bot earlier and is back
    resumed_series: int


async def register(
    uow: UnitOfWork,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    now: datetime,
) -> Registration:
    """Find or create the user; refresh the profile; revive a user who came back."""
    existing = await uow.users.get_by_telegram_id(telegram_id)
    if existing is None:
        user = await uow.users.add(telegram_id, username, first_name, now)
        return Registration(user, created=True, returned=False, resumed_series=0)
    await uow.users.update(existing.id, username=username, first_name=first_name, last_seen_at=now)
    resumed = 0
    returned = not existing.is_active
    if returned:
        resumed = await reactivate_user(uow, existing.id, now)
    refreshed = await uow.users.get(existing.id)
    assert refreshed is not None  # noqa: S101 - just read
    return Registration(refreshed, created=False, returned=returned, resumed_series=resumed)


async def complete_onboarding(uow: UnitOfWork, user_id: int, timezone: str, now: datetime) -> bool:
    """Store the zone, finish onboarding and seed the default categories (once).

    Returns ``True`` the first time (categories were created).
    """
    if not is_valid_timezone(timezone):
        raise InvalidScheduleError(f"unknown timezone: {timezone}")
    user = await uow.users.get(user_id)
    assert user is not None  # noqa: S101 - the caller just loaded it
    first_time = user.onboarded_at is None
    await uow.users.update(user_id, timezone=timezone, onboarded_at=user.onboarded_at or now)
    if first_time and await uow.categories.count(user_id) == 0:
        await create_default_categories(uow, user_id)
    return first_time


async def set_anchors(
    uow: UnitOfWork, user_id: int, *, morning: time, midday: time, evening: time
) -> None:
    if not morning < midday < evening:
        raise InvalidScheduleError("anchors must be ordered: morning < midday < evening")
    await uow.users.update(user_id, morning_time=morning, midday_time=midday, evening_time=evening)
