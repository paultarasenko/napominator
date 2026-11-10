"""Alembic environment (async). The URL comes from DATABASE_URL, not from alembic.ini."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from napominator.infrastructure.db import models  # noqa: F401  (registers tables on Base)
from napominator.infrastructure.db.base import Base
from napominator.infrastructure.db.types import UTCDateTime
from napominator.infrastructure.db.url import (
    DEFAULT_DATABASE_URL,
    is_sqlite,
    normalize_database_url,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    return normalize_database_url(
        explicit or os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
    )


def _render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Keep migrations independent of application code: render our column type as plain SQL."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("import sqlalchemy as sa")
        return "sa.DateTime(timezone=True)"
    return False


def _configure(**kwargs: Any) -> None:
    url = _database_url()
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=is_sqlite(url),
        compare_type=True,
        render_item=_render_item,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
