from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Integer, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from napominator.infrastructure.db.engine import create_engine, create_session_factory, ping
from napominator.infrastructure.db.types import UTCDateTime


class _Base(DeclarativeBase):
    pass


class _Sample(_Base):
    __tablename__ = "sample"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


async def test_ping_and_pragmas(sqlite_engine: AsyncEngine) -> None:
    await ping(sqlite_engine)
    async with sqlite_engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"


async def test_utc_datetime_roundtrip_returns_aware_utc(sqlite_engine: AsyncEngine) -> None:
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = create_session_factory(sqlite_engine)
    plus3 = timezone(timedelta(hours=3))
    async with factory() as session, session.begin():
        session.add(_Sample(id=1, at=datetime(2026, 10, 13, 18, 0, tzinfo=plus3)))
        session.add(_Sample(id=2, at=None))
    async with factory() as session:
        one = await session.get(_Sample, 1)
        two = await session.get(_Sample, 2)
    assert one is not None
    assert one.at == datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    assert one.at is not None
    assert one.at.tzinfo is UTC
    assert two is not None
    assert two.at is None


async def test_naive_datetime_is_rejected(sqlite_engine: AsyncEngine) -> None:
    async with sqlite_engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    factory = create_session_factory(sqlite_engine)
    with pytest.raises(Exception, match="naive datetime"):
        async with factory() as session, session.begin():
            session.add(_Sample(id=1, at=datetime(2026, 1, 1)))


def test_postgres_engine_is_pooled_without_connecting() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@localhost:1/db")
    assert engine.dialect.name == "postgresql"
    assert engine.pool.size() == 10  # type: ignore[attr-defined]


def test_utc_datetime_normalises_aware_values_from_server_dialects() -> None:
    value = datetime(2026, 10, 13, 18, 0, tzinfo=timezone(timedelta(hours=3)))
    dialect = create_engine("postgresql+asyncpg://u:p@localhost:1/db").dialect
    result = UTCDateTime().process_result_value(value, dialect)
    assert result == datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    assert result is not None
    assert result.tzinfo is UTC
