"""Engine and session factory construction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from napominator.infrastructure.db.url import is_sqlite


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
    if is_sqlite(database_url):
        kwargs["connect_args"] = {"timeout": 15}
    else:
        kwargs.update(pool_size=10, max_overflow=10, pool_recycle=1800, pool_timeout=15)
    engine = create_async_engine(database_url, **kwargs)
    if is_sqlite(database_url):
        _install_sqlite_pragmas(engine)
    return engine


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Raise if the database is unreachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
