"""ORM models (SQLAlchemy 2.0 typed mappings). Domain objects live in ``domain.entities``."""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from napominator.infrastructure.db.base import Base
from napominator.infrastructure.db.types import UTCDateTime

_REMINDER_STATUSES = "'active','paused','done','deleted'"
_NOTIFICATION_STATUSES = "'pending','claimed','sent','done','snoozed','skipped','failed','expired'"
_NOTIFICATION_KINDS = "'due','lead','nag','snooze','digest'"
_OPEN = text("status IN ('pending','claimed')")


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str | None] = mapped_column(String(64))
    morning_time: Mapped[time] = mapped_column(Time, default=time(9, 0))
    midday_time: Mapped[time] = mapped_column(Time, default=time(13, 0))
    evening_time: Mapped[time] = mapped_column(Time, default=time(19, 0))
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_time: Mapped[time] = mapped_column(Time, default=time(8, 0))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class CategoryRow(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name_key", name="uq_categories_user_id_name_key"),
        CheckConstraint("length(name) BETWEEN 1 AND 32", name="name_length"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    # case-folded name: SQLite's lower() is ASCII-only, so uniqueness is enforced on this key
    name_key: Mapped[str] = mapped_column(String(32))
    emoji: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)


class ReminderRow(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(f"status IN ({_REMINDER_STATUSES})", name="status_valid"),
        CheckConstraint("length(text) BETWEEN 1 AND 1000", name="text_length"),
        Index("ix_reminders_user_id_status", "user_id", "status"),
        Index("ix_reminders_user_id_category_id", "user_id", "category_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(String(1000))
    tz: Mapped[str] = mapped_column(String(64))
    dtstart_local: Mapped[datetime] = mapped_column(DateTime)  # naive wall-clock time
    rrule: Mapped[str | None] = mapped_column(String(500))
    lead_minutes: Mapped[list[int]] = mapped_column(JSON, default=list)
    nag_interval_min: Mapped[int | None] = mapped_column(Integer)
    nag_max: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class NotificationRow(Base):
    """Transactional outbox: one row per message to send."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(f"status IN ({_NOTIFICATION_STATUSES})", name="status_valid"),
        CheckConstraint(f"kind IN ({_NOTIFICATION_KINDS})", name="kind_valid"),
        UniqueConstraint("dedup_key", name="uq_notifications_dedup_key"),
        # the dispatcher's hot path: only rows that are still open are indexed
        Index(
            "ix_notifications_open_fire_at",
            "fire_at",
            postgresql_where=_OPEN,
            sqlite_where=_OPEN,
        ),
        Index(
            "uq_notifications_user_id_telegram_message_id",
            "user_id",
            "telegram_message_id",
            unique=True,
            postgresql_where=text("telegram_message_id IS NOT NULL"),
            sqlite_where=text("telegram_message_id IS NOT NULL"),
        ),
        Index("ix_notifications_reminder_id_status", "reminder_id", "status"),
        Index("ix_notifications_user_id_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("reminders.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    occurrence_at: Mapped[datetime] = mapped_column(UTCDateTime)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime)
    fire_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    claimed_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    dedup_key: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CategoryHintRow(Base):
    __tablename__ = "category_hints"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    count: Mapped[int] = mapped_column(Integer, default=1)


class ProcessedUpdateRow(Base):
    """Deduplication of Telegram ``update_id`` for state-changing handlers (webhook retries)."""

    __tablename__ = "processed_updates"
    __table_args__ = (Index("ix_processed_updates_processed_at", "processed_at"),)

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime)
