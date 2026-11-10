"""SQLAlchemy implementations of the repository ports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, delete, exists, func, or_, select, type_coerce, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from napominator.domain.categories import name_key
from napominator.domain.entities import (
    OPEN_STATUSES,
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
from napominator.infrastructure.db.models import (
    CategoryHintRow,
    CategoryRow,
    NotificationRow,
    ProcessedUpdateRow,
    ReminderRow,
    UserRow,
)
from napominator.infrastructure.db.types import UTCDateTime

_OPEN = [s.value for s in OPEN_STATUSES]
_DELIVERED = [
    NotificationStatus.SENT.value,
    NotificationStatus.DONE.value,
    NotificationStatus.SNOOZED.value,
]
_LIVE_REMINDER = [ReminderStatus.ACTIVE.value, ReminderStatus.PAUSED.value]
_NEXT_KINDS = [NotificationKind.DUE.value, NotificationKind.SNOOZE.value]
_REACTABLE = [
    NotificationKind.DUE.value,
    NotificationKind.SNOOZE.value,
    NotificationKind.NAG.value,
]
_USER_FIELDS = frozenset(
    {
        "username",
        "first_name",
        "timezone",
        "morning_time",
        "midday_time",
        "evening_time",
        "digest_enabled",
        "digest_time",
        "onboarded_at",
        "last_seen_at",
    }
)
_REMINDER_FIELDS = frozenset(
    {
        "text",
        "category_id",
        "tz",
        "dtstart_local",
        "rrule",
        "lead_minutes",
        "nag_interval_min",
        "nag_max",
        "completed_at",
    }
)
_CATEGORY_FIELDS = frozenset({"name", "name_key", "emoji", "position"})


def _checked(changes: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    unknown = set(changes) - allowed
    if unknown:
        msg = f"fields are not updatable: {sorted(unknown)}"
        raise ValueError(msg)
    return dict(changes)


def _category(row: CategoryRow) -> Category:
    return Category(row.id, row.user_id, row.name, row.emoji, row.position)


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        telegram_id=row.telegram_id,
        username=row.username,
        first_name=row.first_name,
        timezone=row.timezone,
        morning_time=row.morning_time,
        midday_time=row.midday_time,
        evening_time=row.evening_time,
        digest_enabled=row.digest_enabled,
        digest_time=row.digest_time,
        is_active=row.is_active,
        onboarded_at=row.onboarded_at,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


def _reminder(row: ReminderRow) -> Reminder:
    return Reminder(
        id=row.id,
        user_id=row.user_id,
        category_id=row.category_id,
        text=row.text,
        tz=row.tz,
        dtstart_local=row.dtstart_local,
        rrule=row.rrule,
        lead_minutes=tuple(row.lead_minutes or ()),
        nag_interval_min=row.nag_interval_min,
        nag_max=row.nag_max,
        status=ReminderStatus(row.status),
        deleted_at=row.deleted_at,
        completed_at=row.completed_at,
        source_message_id=row.source_message_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _notification(row: NotificationRow) -> Notification:
    return Notification(
        id=row.id,
        user_id=row.user_id,
        reminder_id=row.reminder_id,
        kind=NotificationKind(row.kind),
        occurrence_at=row.occurrence_at,
        scheduled_at=row.scheduled_at,
        fire_at=row.fire_at,
        status=NotificationStatus(row.status),
        claimed_until=row.claimed_until,
        attempts=row.attempts,
        seq=row.seq,
        last_error=row.last_error,
        sent_at=row.sent_at,
        acked_at=row.acked_at,
        telegram_message_id=row.telegram_message_id,
        dedup_key=row.dedup_key,
        created_at=row.created_at,
    )


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: int) -> User | None:
        row = await self._s.get(UserRow, user_id)
        return _user(row) if row else None

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        row = await self._s.scalar(select(UserRow).where(UserRow.telegram_id == telegram_id))
        return _user(row) if row else None

    async def add(
        self, telegram_id: int, username: str | None, first_name: str | None, now: datetime
    ) -> User:
        row = UserRow(
            telegram_id=telegram_id, username=username, first_name=first_name, created_at=now
        )
        self._s.add(row)
        await self._s.flush()
        return _user(row)

    async def set_active(self, user_id: int, *, active: bool) -> None:
        await self._s.execute(update(UserRow).where(UserRow.id == user_id).values(is_active=active))

    async def update(self, user_id: int, **changes: Any) -> None:
        values = _checked(changes, _USER_FIELDS)
        if values:
            await self._s.execute(
                update(UserRow)
                .where(UserRow.id == user_id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )

    async def delete(self, user_id: int) -> bool:
        result = await self._s.execute(
            delete(UserRow)
            .where(UserRow.id == user_id)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]


class SqlReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, new: NewReminder, now: datetime) -> Reminder:
        row = ReminderRow(
            user_id=new.user_id,
            category_id=new.category_id,
            text=new.text,
            tz=new.tz,
            dtstart_local=new.dtstart_local,
            rrule=new.rrule,
            lead_minutes=list(new.lead_minutes),
            nag_interval_min=new.nag_interval_min,
            nag_max=new.nag_max,
            status=ReminderStatus.ACTIVE.value,
            source_message_id=new.source_message_id,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        return _reminder(row)

    async def get(self, reminder_id: int, user_id: int) -> Reminder | None:
        row = await self._s.scalar(
            select(ReminderRow)
            .where(ReminderRow.id == reminder_id, ReminderRow.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return _reminder(row) if row else None

    async def transition(
        self,
        reminder_id: int,
        user_id: int,
        *,
        expected: Sequence[ReminderStatus],
        new: ReminderStatus,
        now: datetime,
    ) -> bool:
        values: dict[str, Any] = {"status": new.value, "updated_at": now}
        if new is ReminderStatus.DELETED:
            values["deleted_at"] = now
        elif new is ReminderStatus.DONE:
            values["completed_at"] = now
        result = await self._s.execute(
            update(ReminderRow)
            .where(
                ReminderRow.id == reminder_id,
                ReminderRow.user_id == user_id,
                ReminderRow.status.in_([s.value for s in expected]),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def update(self, reminder_id: int, user_id: int, now: datetime, **changes: Any) -> bool:
        values = _checked(changes, _REMINDER_FIELDS)
        if "lead_minutes" in values:
            values["lead_minutes"] = list(values["lead_minutes"])
        result = await self._s.execute(
            update(ReminderRow)
            .where(ReminderRow.id == reminder_id, ReminderRow.user_id == user_id)
            .values(updated_at=now, **values)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    @staticmethod
    def _next_at() -> Any:
        return (
            select(type_coerce(func.min(NotificationRow.fire_at), UTCDateTime))
            .where(
                NotificationRow.reminder_id == ReminderRow.id,
                NotificationRow.kind.in_(_NEXT_KINDS),
                NotificationRow.status.in_(_OPEN),
            )
            .correlate(ReminderRow)
            .scalar_subquery()
        )

    def _conditions(self, user_id: int, flt: ReminderFilter, next_at: Any) -> list[Any]:
        where: list[Any] = [
            ReminderRow.user_id == user_id,
            ReminderRow.status.in_([s.value for s in flt.statuses]),
        ]
        if flt.category_id is not None:
            where.append(ReminderRow.category_id == flt.category_id)
        if flt.uncategorized:
            where.append(ReminderRow.category_id.is_(None))
        if flt.recurring is True:
            where.append(ReminderRow.rrule.is_not(None))
        elif flt.recurring is False:
            where.append(ReminderRow.rrule.is_(None))
        if flt.next_from is not None:
            where.append(next_at >= flt.next_from)
        if flt.next_to is not None:
            where.append(next_at < flt.next_to)
        return where

    async def search(
        self, user_id: int, flt: ReminderFilter, *, limit: int, offset: int = 0
    ) -> list[ReminderView]:
        next_at = self._next_at()
        order: list[Any]
        if flt.order == "completed":
            order = [ReminderRow.completed_at.desc(), ReminderRow.id.desc()]
        elif flt.order == "created":
            order = [ReminderRow.created_at.desc(), ReminderRow.id.desc()]
        else:
            order = [case((next_at.is_(None), 1), else_=0), next_at, ReminderRow.id]
        rows = await self._s.execute(
            select(ReminderRow, next_at.label("next_at"))
            .where(*self._conditions(user_id, flt, next_at))
            .order_by(*order)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return [ReminderView(_reminder(row), moment) for row, moment in rows]

    async def count(self, user_id: int, flt: ReminderFilter) -> int:
        next_at = self._next_at()
        total = await self._s.scalar(
            select(func.count())
            .select_from(ReminderRow)
            .where(*self._conditions(user_id, flt, next_at))
        )
        return int(total or 0)

    async def all_for_user(self, user_id: int) -> list[Reminder]:
        rows = await self._s.scalars(
            select(ReminderRow).where(ReminderRow.user_id == user_id).order_by(ReminderRow.id)
        )
        return [_reminder(r) for r in rows]

    async def purge(self, *, done_before: datetime, deleted_before: datetime) -> int:
        result = await self._s.execute(
            delete(ReminderRow)
            .where(
                or_(
                    and_(
                        ReminderRow.status == ReminderStatus.DONE.value,
                        ReminderRow.completed_at < done_before,
                    ),
                    and_(
                        ReminderRow.status == ReminderStatus.DELETED.value,
                        ReminderRow.deleted_at < deleted_before,
                    ),
                )
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def list_live(self, user_id: int) -> list[Reminder]:
        rows = await self._s.scalars(
            select(ReminderRow)
            .where(ReminderRow.user_id == user_id, ReminderRow.status.in_(_LIVE_REMINDER))
            .order_by(ReminderRow.id)
        )
        return [_reminder(r) for r in rows]

    async def count_active(self, user_id: int) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(ReminderRow)
            .where(ReminderRow.user_id == user_id, ReminderRow.status.in_(_LIVE_REMINDER))
        )
        return int(count or 0)

    async def recurring_without_open_notification(self, user_id: int) -> list[Reminder]:
        has_open = exists().where(
            NotificationRow.reminder_id == ReminderRow.id,
            NotificationRow.kind == NotificationKind.DUE.value,
            NotificationRow.status.in_(_OPEN),
        )
        rows = await self._s.scalars(
            select(ReminderRow)
            .where(
                ReminderRow.user_id == user_id,
                ReminderRow.status == ReminderStatus.ACTIVE.value,
                ReminderRow.rrule.is_not(None),
                ~has_open,
            )
            .order_by(ReminderRow.id)
        )
        return [_reminder(r) for r in rows]

    async def close_one_off_without_open_notification(self, user_id: int, now: datetime) -> int:
        has_open = exists().where(
            NotificationRow.reminder_id == ReminderRow.id, NotificationRow.status.in_(_OPEN)
        )
        result = await self._s.execute(
            update(ReminderRow)
            .where(
                ReminderRow.user_id == user_id,
                ReminderRow.status == ReminderStatus.ACTIVE.value,
                ReminderRow.rrule.is_(None),
                ~has_open,
            )
            .values(status=ReminderStatus.DONE.value, completed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


class SqlNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def _dialect(self) -> str:
        return self._s.get_bind().dialect.name

    async def add(self, new: NewNotification, now: datetime) -> bool:
        """Idempotent insert. Returns ``True`` if a row was created or revived.

        A repeated key is a no-op, except that a ``skipped`` row (e.g. the user had blocked
        the bot) is revived so that an intentional re-schedule actually takes effect.
        """
        insert = pg_insert if self._dialect == "postgresql" else sqlite_insert
        statement = insert(NotificationRow).values(
            user_id=new.user_id,
            reminder_id=new.reminder_id,
            kind=new.kind.value,
            occurrence_at=new.occurrence_at,
            scheduled_at=new.fire_at,
            fire_at=new.fire_at,
            status=NotificationStatus.PENDING.value,
            attempts=0,
            seq=new.seq,
            dedup_key=new.dedup_key,
            created_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["dedup_key"],
            set_={
                "status": NotificationStatus.PENDING.value,
                "scheduled_at": new.fire_at,
                "fire_at": new.fire_at,
                "attempts": 0,
                "claimed_until": None,
                "last_error": None,
            },
            where=NotificationRow.status == NotificationStatus.SKIPPED.value,
        )
        result = await self._s.execute(statement)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def claim_due(
        self, now: datetime, limit: int, lease: timedelta
    ) -> list[ClaimedNotification]:
        due = or_(
            and_(
                NotificationRow.status == NotificationStatus.PENDING.value,
                NotificationRow.fire_at <= now,
            ),
            and_(
                NotificationRow.status == NotificationStatus.CLAIMED.value,
                NotificationRow.claimed_until <= now,
            ),
        )
        picked = (
            select(NotificationRow.id)
            .where(due)
            .order_by(NotificationRow.fire_at, NotificationRow.id)
            .limit(limit)
        )
        if self._dialect == "postgresql":
            picked = picked.with_for_update(skip_locked=True)
        claimed = await self._s.execute(
            update(NotificationRow)
            .where(NotificationRow.id.in_(picked.scalar_subquery()))
            .values(
                status=NotificationStatus.CLAIMED.value,
                claimed_until=now + lease,
                attempts=NotificationRow.attempts + 1,
            )
            .returning(NotificationRow.id)
            .execution_options(synchronize_session=False)
        )
        ids = [row[0] for row in claimed]
        if not ids:
            return []
        rows = await self._s.execute(
            select(NotificationRow, UserRow, ReminderRow)
            .join(UserRow, UserRow.id == NotificationRow.user_id)
            .outerjoin(ReminderRow, ReminderRow.id == NotificationRow.reminder_id)
            .where(NotificationRow.id.in_(ids))
            .order_by(NotificationRow.fire_at, NotificationRow.id)
            .execution_options(populate_existing=True)
        )
        return [
            ClaimedNotification(
                notification=_notification(n),
                user=_user(u),
                reminder=_reminder(r) if r is not None else None,
            )
            for n, u, r in rows
        ]

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
        values: dict[str, Any] = {
            "status": status.value,
            "claimed_until": None,
            "last_error": error[:500] if error else None,
        }
        if status is NotificationStatus.SENT:
            values["sent_at"] = now
            values["telegram_message_id"] = message_id
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.id == notification_id,
                NotificationRow.status == NotificationStatus.CLAIMED.value,
                NotificationRow.attempts == attempts,  # fencing token: still our lease
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def release(
        self,
        notification_id: int,
        *,
        attempts: int,
        fire_at: datetime,
        error: str | None,
        count_attempt: bool,
    ) -> bool:
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.id == notification_id,
                NotificationRow.status == NotificationStatus.CLAIMED.value,
                NotificationRow.attempts == attempts,
            )
            .values(
                status=NotificationStatus.PENDING.value,
                fire_at=fire_at,
                claimed_until=None,
                last_error=error[:500] if error else None,
                attempts=attempts if count_attempt else attempts - 1,
            )
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def _skip(self, condition: Any, reason: str) -> int:
        result = await self._s.execute(
            update(NotificationRow)
            .where(condition, NotificationRow.status.in_(_OPEN))
            .values(status=NotificationStatus.SKIPPED.value, claimed_until=None, last_error=reason)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def skip_open_for_user(self, user_id: int, reason: str) -> int:
        return await self._skip(NotificationRow.user_id == user_id, reason)

    async def skip_open_for_reminder(self, reminder_id: int, reason: str) -> int:
        return await self._skip(NotificationRow.reminder_id == reminder_id, reason)

    async def has_open_for_reminder(self, reminder_id: int) -> bool:
        found = await self._s.scalar(
            select(NotificationRow.id)
            .where(NotificationRow.reminder_id == reminder_id, NotificationRow.status.in_(_OPEN))
            .limit(1)
        )
        return found is not None

    async def earliest_open_fire_at(self) -> datetime | None:
        """Next moment the dispatcher has something to do (a fire time or a lease expiry)."""
        moment = case(
            (NotificationRow.status == NotificationStatus.PENDING.value, NotificationRow.fire_at),
            else_=NotificationRow.claimed_until,
        )
        return await self._s.scalar(
            select(type_coerce(func.min(moment), UTCDateTime)).where(
                NotificationRow.status.in_(_OPEN)
            )
        )

    async def queue_lag(self, now: datetime) -> timedelta:
        oldest = await self._s.scalar(
            select(type_coerce(func.min(NotificationRow.fire_at), UTCDateTime)).where(
                NotificationRow.status == NotificationStatus.PENDING.value,
                NotificationRow.fire_at <= now,
            )
        )
        return max(now - oldest, timedelta(0)) if oldest else timedelta(0)

    async def count_sent_since(self, user_id: int, since: datetime) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.status.in_(_DELIVERED),
                NotificationRow.sent_at >= since,
            )
        )
        return int(count or 0)

    async def get(self, notification_id: int) -> Notification | None:
        row = await self._s.scalar(
            select(NotificationRow)
            .where(NotificationRow.id == notification_id)
            .execution_options(populate_existing=True)
        )
        return _notification(row) if row else None

    async def get_for_user(self, notification_id: int, user_id: int) -> Notification | None:
        row = await self._s.scalar(
            select(NotificationRow)
            .where(NotificationRow.id == notification_id, NotificationRow.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return _notification(row) if row else None

    async def get_by_message(self, user_id: int, telegram_message_id: int) -> Notification | None:
        row = await self._s.scalar(
            select(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.telegram_message_id == telegram_message_id,
            )
            .execution_options(populate_existing=True)
        )
        return _notification(row) if row else None

    async def transition(
        self,
        notification_id: int,
        user_id: int,
        *,
        expected: Sequence[NotificationStatus],
        new: NotificationStatus,
        acked_at: datetime | None,
    ) -> bool:
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.id == notification_id,
                NotificationRow.user_id == user_id,
                NotificationRow.status.in_([s.value for s in expected]),
            )
            .values(status=new.value, acked_at=acked_at)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def awaiting_ack(self, user_id: int, since: datetime, limit: int) -> list[AwaitingAck]:
        rows = await self._s.execute(
            select(NotificationRow, ReminderRow)
            .join(ReminderRow, ReminderRow.id == NotificationRow.reminder_id)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.kind.in_(_NEXT_KINDS),
                NotificationRow.status == NotificationStatus.SENT.value,
                NotificationRow.acked_at.is_(None),
                NotificationRow.sent_at >= since,
                ReminderRow.status != ReminderStatus.DELETED.value,
            )
            .order_by(NotificationRow.sent_at.desc(), NotificationRow.id.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return [AwaitingAck(_notification(n), _reminder(r)) for n, r in rows]

    async def ack_group(
        self,
        user_id: int,
        reminder_id: int,
        occurrence_at: datetime,
        *,
        new: NotificationStatus,
        acked_at: datetime,
    ) -> int:
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.reminder_id == reminder_id,
                NotificationRow.occurrence_at == occurrence_at,
                NotificationRow.kind.in_(_REACTABLE),
                NotificationRow.status == NotificationStatus.SENT.value,
            )
            .values(status=new.value, acked_at=acked_at)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def unack_group(
        self, user_id: int, reminder_id: int, occurrence_at: datetime, acked_at: datetime
    ) -> int:
        result = await self._s.execute(
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.reminder_id == reminder_id,
                NotificationRow.occurrence_at == occurrence_at,
                NotificationRow.kind.in_(_REACTABLE),
                NotificationRow.acked_at == acked_at,
                NotificationRow.status.in_(
                    [NotificationStatus.DONE.value, NotificationStatus.SNOOZED.value]
                ),
            )
            .values(status=NotificationStatus.SENT.value, acked_at=None)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def skip_open(
        self, reminder_id: int, kinds: Sequence[NotificationKind], reason: str
    ) -> int:
        return await self._skip(
            and_(
                NotificationRow.reminder_id == reminder_id,
                NotificationRow.kind.in_([k.value for k in kinds]),
            ),
            reason,
        )

    async def all_for_user(self, user_id: int) -> list[Notification]:
        rows = await self._s.scalars(
            select(NotificationRow)
            .where(NotificationRow.user_id == user_id)
            .order_by(NotificationRow.fire_at, NotificationRow.id)
        )
        return [_notification(r) for r in rows]

    async def purge_history(self, before: datetime) -> int:
        result = await self._s.execute(
            delete(NotificationRow)
            .where(NotificationRow.status.not_in(_OPEN), NotificationRow.scheduled_at < before)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def list_for_reminder(self, reminder_id: int) -> list[Notification]:
        rows = await self._s.scalars(
            select(NotificationRow)
            .where(NotificationRow.reminder_id == reminder_id)
            .order_by(NotificationRow.fire_at, NotificationRow.id)
            .execution_options(populate_existing=True)
        )
        return [_notification(r) for r in rows]


class SqlCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(self, user_id: int) -> list[Category]:
        rows = await self._s.scalars(
            select(CategoryRow)
            .where(CategoryRow.user_id == user_id)
            .order_by(CategoryRow.position, CategoryRow.id)
            .execution_options(populate_existing=True)
        )
        return [_category(r) for r in rows]

    async def get(self, category_id: int, user_id: int) -> Category | None:
        row = await self._s.scalar(
            select(CategoryRow)
            .where(CategoryRow.id == category_id, CategoryRow.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return _category(row) if row else None

    async def add(self, user_id: int, name: str, emoji: str | None) -> Category | None:
        key = name_key(name)
        taken = await self._s.scalar(
            select(CategoryRow.id).where(
                CategoryRow.user_id == user_id, CategoryRow.name_key == key
            )
        )
        if taken is not None:
            return None
        last = await self._s.scalar(
            select(func.max(CategoryRow.position)).where(CategoryRow.user_id == user_id)
        )
        row = CategoryRow(
            user_id=user_id, name=name, name_key=key, emoji=emoji, position=(last or 0) + 1
        )
        self._s.add(row)
        await self._s.flush()
        return _category(row)

    async def update(self, category_id: int, user_id: int, **changes: Any) -> bool:
        values = _checked(changes, _CATEGORY_FIELDS)
        if "name" in values and "name_key" not in values:
            values["name_key"] = name_key(values["name"])
        if "name_key" in values:
            clash = await self._s.scalar(
                select(CategoryRow.id).where(
                    CategoryRow.user_id == user_id,
                    CategoryRow.name_key == values["name_key"],
                    CategoryRow.id != category_id,
                )
            )
            if clash is not None:
                return False
        result = await self._s.execute(
            update(CategoryRow)
            .where(CategoryRow.id == category_id, CategoryRow.user_id == user_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def delete(self, category_id: int, user_id: int) -> bool:
        result = await self._s.execute(
            delete(CategoryRow)
            .where(CategoryRow.id == category_id, CategoryRow.user_id == user_id)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def count(self, user_id: int) -> int:
        total = await self._s.scalar(
            select(func.count()).select_from(CategoryRow).where(CategoryRow.user_id == user_id)
        )
        return int(total or 0)

    async def count_reminders(self, category_id: int, user_id: int) -> int:
        total = await self._s.scalar(
            select(func.count())
            .select_from(ReminderRow)
            .where(
                ReminderRow.user_id == user_id,
                ReminderRow.category_id == category_id,
                ReminderRow.status.in_(_LIVE_REMINDER),
            )
        )
        return int(total or 0)

    async def move_reminders(self, user_id: int, from_id: int, to_id: int | None) -> int:
        result = await self._s.execute(
            update(ReminderRow)
            .where(ReminderRow.user_id == user_id, ReminderRow.category_id == from_id)
            .values(category_id=to_id)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def swap(self, user_id: int, category_id: int, direction: int) -> bool:
        """Move a category one step up (``-1``) or down (``+1``) in the user's order."""
        ordered = await self.list(user_id)
        index = next((i for i, c in enumerate(ordered) if c.id == category_id), None)
        if index is None or not 0 <= index + direction < len(ordered):
            return False
        ids = [c.id for c in ordered]
        ids[index], ids[index + direction] = ids[index + direction], ids[index]
        for position, cid in enumerate(ids, start=1):  # renumber: positions may have gaps/ties
            await self._s.execute(
                update(CategoryRow)
                .where(CategoryRow.id == cid, CategoryRow.user_id == user_id)
                .values(position=position)
                .execution_options(synchronize_session=False)
            )
        return True


class SqlHintRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def _dialect(self) -> str:
        return self._s.get_bind().dialect.name

    async def bump(self, user_id: int, tokens: Sequence[str], category_id: int) -> None:
        insert = pg_insert if self._dialect == "postgresql" else sqlite_insert
        for token in dict.fromkeys(t[:64] for t in tokens if t):
            statement = insert(CategoryHintRow).values(
                user_id=user_id, token=token, category_id=category_id, count=1
            )
            await self._s.execute(
                statement.on_conflict_do_update(
                    index_elements=["user_id", "token"],
                    set_={
                        "category_id": category_id,
                        # same category -> reinforce; a different one replaces the old habit
                        "count": case(
                            (CategoryHintRow.category_id == category_id, CategoryHintRow.count + 1),
                            else_=1,
                        ),
                    },
                )
            )

    async def learned(self, user_id: int, tokens: Sequence[str]) -> Mapping[str, tuple[int, int]]:
        if not tokens:
            return {}
        rows = await self._s.execute(
            select(CategoryHintRow.token, CategoryHintRow.category_id, CategoryHintRow.count).where(
                CategoryHintRow.user_id == user_id, CategoryHintRow.token.in_(list(tokens))
            )
        )
        return {token: (category_id, count) for token, category_id, count in rows}

    async def all_for_user(self, user_id: int) -> list[tuple[str, int, int]]:
        rows = await self._s.execute(
            select(CategoryHintRow.token, CategoryHintRow.category_id, CategoryHintRow.count)
            .where(CategoryHintRow.user_id == user_id)
            .order_by(CategoryHintRow.token)
        )
        return [(token, cid, count) for token, cid, count in rows]


class SqlProcessedUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def _dialect(self) -> str:
        return self._s.get_bind().dialect.name

    async def register(self, update_id: int, now: datetime) -> bool:
        insert = pg_insert if self._dialect == "postgresql" else sqlite_insert
        result = await self._s.execute(
            insert(ProcessedUpdateRow)
            .values(update_id=update_id, processed_at=now)
            .on_conflict_do_nothing(index_elements=["update_id"])
        )
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def purge_before(self, cutoff: datetime) -> int:
        result = await self._s.execute(
            delete(ProcessedUpdateRow).where(ProcessedUpdateRow.processed_at < cutoff)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


class SqlAdminRepository:
    """Aggregate numbers for ``/admin``: never returns reminder text or user names."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _count(self, *conditions: Any, table: Any) -> int:
        total = await self._s.scalar(select(func.count()).select_from(table).where(*conditions))
        return int(total or 0)

    async def snapshot(self, now: datetime) -> AdminSnapshot:
        since = now - timedelta(hours=24)
        failed = [NotificationStatus.FAILED.value, NotificationStatus.EXPIRED.value]
        lag_row = await self._s.scalar(
            select(type_coerce(func.min(NotificationRow.fire_at), UTCDateTime)).where(
                NotificationRow.status == NotificationStatus.PENDING.value,
                NotificationRow.fire_at <= now,
            )
        )
        errors = await self._s.execute(
            select(NotificationRow.kind, NotificationRow.status, NotificationRow.last_error)
            .where(NotificationRow.last_error.is_not(None), NotificationRow.scheduled_at >= since)
            .order_by(NotificationRow.id.desc())
            .limit(5)
        )
        return AdminSnapshot(
            users_total=await self._count(table=UserRow),
            users_active=await self._count(UserRow.is_active.is_(True), table=UserRow),
            reminders_active=await self._count(
                ReminderRow.status == ReminderStatus.ACTIVE.value, table=ReminderRow
            ),
            notifications_open=await self._count(
                NotificationRow.status.in_(_OPEN), table=NotificationRow
            ),
            queue_lag_seconds=max((now - lag_row).total_seconds(), 0.0) if lag_row else 0.0,
            failures_24h=await self._count(
                NotificationRow.status.in_(failed),
                NotificationRow.scheduled_at >= since,
                table=NotificationRow,
            ),
            recent_errors=tuple((k, st, (err or "")[:120]) for k, st, err in errors),
        )
