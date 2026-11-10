"""Alembic: upgrade from scratch, no model/migration drift, downgrade round trip."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from napominator.infrastructure.db import models  # noqa: F401
from napominator.infrastructure.db.base import Base

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "users",
    "categories",
    "reminders",
    "notifications",
    "category_hints",
    "processed_updates",
    "alembic_version",
}


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def database_url(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """A brand-new, empty database; ``DATABASE_URL`` points Alembic at it."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'migrate.db'}"
        monkeypatch.setenv("DATABASE_URL", url)
        yield f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}"
        return
    admin_url = request.getfixturevalue("postgres_url")
    name = f"mig_{uuid.uuid4().hex[:10]}"

    async def run(sql: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.exec_driver_sql(sql)
        await engine.dispose()

    asyncio.run(run(f'CREATE DATABASE "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    try:
        yield url
    finally:
        asyncio.run(run(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
def alembic_cfg() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _inspect[T](url: str, fn: Callable[[Connection], T]) -> T:
    async def go() -> T:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(fn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_upgrade_head_creates_every_table(alembic_cfg: Config, database_url: str) -> None:
    command.upgrade(alembic_cfg, "head")
    tables = _inspect(database_url, lambda c: set(inspect(c).get_table_names()))
    assert tables == EXPECTED_TABLES


def test_models_and_migrations_do_not_drift(alembic_cfg: Config, database_url: str) -> None:
    command.upgrade(alembic_cfg, "head")

    def diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    assert _inspect(database_url, diff) == []


def test_hot_path_indexes_exist(alembic_cfg: Config, database_url: str) -> None:
    command.upgrade(alembic_cfg, "head")
    indexes = _inspect(
        database_url,
        lambda c: {i["name"] for i in inspect(c).get_indexes("notifications")},
    )
    assert {
        "ix_notifications_open_fire_at",
        "uq_notifications_user_id_telegram_message_id",
    } <= indexes


def test_downgrade_and_upgrade_again(alembic_cfg: Config, database_url: str) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    tables = _inspect(database_url, lambda c: set(inspect(c).get_table_names()))
    assert tables <= {"alembic_version"}
    command.upgrade(alembic_cfg, "head")
    tables = _inspect(database_url, lambda c: set(inspect(c).get_table_names()))
    assert tables == EXPECTED_TABLES


def test_migration_file_does_not_import_application_code() -> None:
    sources = [p.read_text() for p in (ROOT / "migrations" / "versions").glob("*.py")]
    assert sources
    assert not any("napominator" in s for s in sources)
