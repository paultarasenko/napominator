from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from napominator.infrastructure.db import models  # noqa: F401  (registers the tables)
from napominator.infrastructure.db.base import Base
from napominator.infrastructure.db.engine import create_engine, create_session_factory
from napominator.infrastructure.db.uow import SqlAlchemyUnitOfWork
from napominator.infrastructure.db.url import normalize_database_url
from tests.support import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def sqlite_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """TEST_POSTGRES_URL (CI service container) or a throw-away local server via pgserver."""
    explicit = os.environ.get("TEST_POSTGRES_URL")
    if explicit:
        yield normalize_database_url(explicit)
        return
    try:
        import pgserver  # noqa: PLC0415 - optional dependency, only needed here
    except ImportError:
        pytest.skip("no PostgreSQL available (set TEST_POSTGRES_URL or install pgserver)")
    data_dir = tempfile.mkdtemp(prefix="napominator-pg-")
    server = pgserver.get_server(data_dir, cleanup_mode="stop")  # type: ignore[attr-defined]
    try:
        yield normalize_database_url(server.get_uri().replace(":@", "@", 1))
    finally:
        server.cleanup()
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
async def engine(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A fresh schema on every supported dialect."""
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    else:
        url = request.getfixturevalue("postgres_url")
    eng = create_engine(url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], SqlAlchemyUnitOfWork]:
    def make() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return make
