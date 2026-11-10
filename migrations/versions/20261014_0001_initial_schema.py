"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-14 21:23:58.543874
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("update_id", name=op.f("pk_processed_updates")),
    )
    with op.batch_alter_table("processed_updates", schema=None) as batch_op:
        batch_op.create_index("ix_processed_updates_processed_at", ["processed_at"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("morning_time", sa.Time(), nullable=False),
        sa.Column("midday_time", sa.Time(), nullable=False),
        sa.Column("evening_time", sa.Time(), nullable=False),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_id", name=op.f("uq_users_telegram_id")),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("name_key", sa.String(length=32), nullable=False),
        sa.Column("emoji", sa.String(length=16), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 32", name=op.f("ck_categories_name_length")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_categories_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("user_id", "name_key", name="uq_categories_user_id_name_key"),
    )
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_categories_user_id"), ["user_id"], unique=False)

    op.create_table(
        "category_hints",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_category_hints_category_id_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_category_hints_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "token", name=op.f("pk_category_hints")),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("tz", sa.String(length=64), nullable=False),
        sa.Column("dtstart_local", sa.DateTime(), nullable=False),
        sa.Column("rrule", sa.String(length=500), nullable=True),
        sa.Column("lead_minutes", sa.JSON(), nullable=False),
        sa.Column("nag_interval_min", sa.Integer(), nullable=True),
        sa.Column("nag_max", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused','done','deleted')", name=op.f("ck_reminders_status_valid")
        ),
        sa.CheckConstraint(
            "length(text) BETWEEN 1 AND 1000", name=op.f("ck_reminders_text_length")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_reminders_category_id_categories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_reminders_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    with op.batch_alter_table("reminders", schema=None) as batch_op:
        batch_op.create_index(
            "ix_reminders_user_id_category_id", ["user_id", "category_id"], unique=False
        )
        batch_op.create_index("ix_reminders_user_id_status", ["user_id", "status"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reminder_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('due','lead','nag','snooze','digest')",
            name=op.f("ck_notifications_kind_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','sent','done','snoozed','skipped','failed','expired')",
            name=op.f("ck_notifications_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["reminder_id"],
            ["reminders.id"],
            name=op.f("fk_notifications_reminder_id_reminders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("dedup_key", name="uq_notifications_dedup_key"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(
            "ix_notifications_open_fire_at",
            ["fire_at"],
            unique=False,
            postgresql_where=sa.text("status IN ('pending','claimed')"),
            sqlite_where=sa.text("status IN ('pending','claimed')"),
        )
        batch_op.create_index(
            "ix_notifications_reminder_id_status", ["reminder_id", "status"], unique=False
        )
        batch_op.create_index(
            "ix_notifications_user_id_status", ["user_id", "status"], unique=False
        )
        batch_op.create_index(
            "uq_notifications_user_id_telegram_message_id",
            ["user_id", "telegram_message_id"],
            unique=True,
            postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
            sqlite_where=sa.text("telegram_message_id IS NOT NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(
            "uq_notifications_user_id_telegram_message_id",
            postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
            sqlite_where=sa.text("telegram_message_id IS NOT NULL"),
        )
        batch_op.drop_index("ix_notifications_user_id_status")
        batch_op.drop_index("ix_notifications_reminder_id_status")
        batch_op.drop_index(
            "ix_notifications_open_fire_at",
            postgresql_where=sa.text("status IN ('pending','claimed')"),
            sqlite_where=sa.text("status IN ('pending','claimed')"),
        )

    op.drop_table("notifications")
    with op.batch_alter_table("reminders", schema=None) as batch_op:
        batch_op.drop_index("ix_reminders_user_id_status")
        batch_op.drop_index("ix_reminders_user_id_category_id")

    op.drop_table("reminders")
    op.drop_table("category_hints")
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_categories_user_id"))

    op.drop_table("categories")
    op.drop_table("users")
    with op.batch_alter_table("processed_updates", schema=None) as batch_op:
        batch_op.drop_index("ix_processed_updates_processed_at")

    op.drop_table("processed_updates")
