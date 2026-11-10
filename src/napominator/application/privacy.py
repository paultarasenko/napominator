"""Data export, account deletion and retention (spec M10)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from napominator.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from napominator.domain.entities import User
from napominator.infrastructure.logging import get_logger

log = get_logger("privacy")
EXPORT_VERSION = 1


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def export_user(uow: UnitOfWork, user: User, now: datetime) -> dict[str, Any]:
    """Everything stored about the user, as plain JSON-able data (no internal secrets)."""
    cats = await uow.categories.list(user.id)
    names = {c.id: c.name for c in cats}
    reminders = await uow.reminders.all_for_user(user.id)
    notifications = await uow.notifications.all_for_user(user.id)
    hints = await uow.hints.all_for_user(user.id)
    return {
        "format": "napominator-export",
        "version": EXPORT_VERSION,
        "exported_at": _iso(now),
        "profile": {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "timezone": user.timezone,
            "morning_time": user.morning_time.isoformat(timespec="minutes"),
            "midday_time": user.midday_time.isoformat(timespec="minutes"),
            "evening_time": user.evening_time.isoformat(timespec="minutes"),
            "digest_enabled": user.digest_enabled,
            "digest_time": user.digest_time.isoformat(timespec="minutes"),
            "registered_at": _iso(user.created_at),
        },
        "categories": [{"name": c.name, "emoji": c.emoji, "position": c.position} for c in cats],
        "reminders": [
            {
                "id": r.id,
                "text": r.text,
                "category": names.get(r.category_id) if r.category_id else None,
                "timezone": r.tz,
                "first_local_time": r.dtstart_local.isoformat(),
                "repeat_rule": r.rrule,
                "lead_minutes": list(r.lead_minutes),
                "nag_interval_minutes": r.nag_interval_min,
                "nag_max": r.nag_max,
                "status": r.status.value,
                "created_at": _iso(r.created_at),
                "completed_at": _iso(r.completed_at),
                "deleted_at": _iso(r.deleted_at),
            }
            for r in reminders
        ],
        "deliveries": [
            {
                "reminder_id": n.reminder_id,
                "kind": n.kind.value,
                "status": n.status.value,
                "occurrence_at": _iso(n.occurrence_at),
                "scheduled_at": _iso(n.scheduled_at),
                "sent_at": _iso(n.sent_at),
                "acknowledged_at": _iso(n.acked_at),
            }
            for n in notifications
        ],
        "category_hints": [
            {"word": token, "category": names.get(cid), "count": count}
            for token, cid, count in hints
        ],
    }


def to_json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


@dataclass(frozen=True, slots=True)
class DeletionSummary:
    reminders: int
    categories: int


async def deletion_preview(uow: UnitOfWork, user_id: int) -> DeletionSummary:
    return DeletionSummary(
        reminders=len(await uow.reminders.all_for_user(user_id)),
        categories=await uow.categories.count(user_id),
    )


async def delete_account(uow: UnitOfWork, user_id: int) -> DeletionSummary:
    """Remove the user and all their data (foreign keys cascade). Irreversible."""
    summary = await deletion_preview(uow, user_id)
    await uow.users.delete(user_id)
    return summary


# --- retention ---


@dataclass(frozen=True, slots=True)
class Retention:
    done_days: int = 90  # finished reminders
    deleted_days: int = 7  # soft-deleted reminders (the "undo" window)
    history_days: int = 90  # delivery history of repeating reminders
    updates_days: int = 3  # Telegram update ids kept for de-duplication
    interval_seconds: float = 6 * 3600.0


@dataclass(frozen=True, slots=True)
class PurgeResult:
    reminders: int
    notifications: int
    updates: int


async def purge(uow: UnitOfWork, now: datetime, retention: Retention) -> PurgeResult:
    reminders = await uow.reminders.purge(
        done_before=now - timedelta(days=retention.done_days),
        deleted_before=now - timedelta(days=retention.deleted_days),
    )
    notifications = await uow.notifications.purge_history(
        now - timedelta(days=retention.history_days)
    )
    updates = await uow.processed_updates.purge_before(now - timedelta(days=retention.updates_days))
    return PurgeResult(reminders, notifications, updates)


class Janitor:
    """Periodically applies the retention policy."""

    def __init__(
        self, uow_factory: UnitOfWorkFactory, clock: Clock, retention: Retention | None = None
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._retention = retention or Retention()

    async def run_once(self) -> PurgeResult:
        async with self._uow() as uow:
            result = await purge(uow, self._clock.now(), self._retention)
        if result.reminders or result.notifications:
            log.info(
                "retention_purged",
                reminders=result.reminders,
                notifications=result.notifications,
                updates=result.updates,
            )
        return result

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # never let housekeeping take the bot down
                log.error("retention_failed", error=type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), self._retention.interval_seconds)
            except TimeoutError:
                continue
