from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher

from napominator import main as app_main
from napominator.infrastructure.config import Settings
from napominator.infrastructure.db.base import Base
from napominator.infrastructure.db.engine import create_engine

TOKEN = "123456:ABCDEF-token-for-tests"


@contextlib.asynccontextmanager
async def engine_with_schema(url: str) -> AsyncIterator[None]:
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield


def settings(db: str, token: str = TOKEN) -> Settings:
    return Settings(_env_file=None, bot_token=token, database_url=db)


async def test_run_starts_polling_runs_the_dispatcher_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polling = AsyncMock()
    monkeypatch.setattr(Dispatcher, "start_polling", polling)
    set_commands = AsyncMock()
    monkeypatch.setattr(Bot, "set_my_commands", set_commands)
    cfg = settings(f"sqlite:///{tmp_path / 'ok.db'}")
    cfg = cfg.model_copy(update={"heartbeat_path": str(tmp_path / "hb")})
    async with engine_with_schema(cfg.database_url):
        await app_main.run(cfg)
    polling.assert_awaited_once()
    set_commands.assert_awaited_once()
    registered = set_commands.await_args
    assert registered is not None
    assert {c.command for c in registered.args[0]} >= {"new", "today", "help"}


async def test_run_exits_with_clear_message_when_db_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "no-such-dir" / "x.db"
    with pytest.raises(SystemExit) as info:
        await app_main.run(settings(f"sqlite:///{missing}"))
    assert info.value.code == app_main.EXIT_STARTUP_ERROR
    assert "DATABASE_URL" in capsys.readouterr().err


async def test_run_exits_on_malformed_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        await app_main.run(settings(f"sqlite:///{tmp_path / 'a.db'}", token="x" * 12))
    assert info.value.code == app_main.EXIT_CONFIG_ERROR
    assert "invalid format" in capsys.readouterr().err


def test_main_wires_settings_logging_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = settings(f"sqlite:///{tmp_path / 'm.db'}")
    ran: list[Settings] = []

    async def fake_run(s: Settings) -> None:
        ran.append(s)

    monkeypatch.setattr(app_main, "load_settings", lambda: cfg)
    monkeypatch.setattr(app_main, "run", fake_run)
    app_main.main()
    assert ran == [cfg]


async def test_a_telegram_outage_at_startup_is_retried_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram.exceptions import TelegramNetworkError  # noqa: PLC0415
    from aiogram.methods import GetMe  # noqa: PLC0415

    attempts = AsyncMock(
        side_effect=[
            TelegramNetworkError(GetMe(), "down"),
            TelegramNetworkError(GetMe(), "still down"),
            None,
        ]
    )
    sleeps = AsyncMock(return_value=False)  # no stop signal while waiting
    monkeypatch.setattr(Dispatcher, "start_polling", attempts)
    monkeypatch.setattr(Bot, "set_my_commands", AsyncMock())
    monkeypatch.setattr(app_main, "_sleep", sleeps)
    cfg = settings(f"sqlite:///{tmp_path / 'o.db'}")
    cfg = cfg.model_copy(update={"heartbeat_path": str(tmp_path / "hb")})
    async with engine_with_schema(cfg.database_url):
        await app_main.run(cfg)
    assert attempts.await_count == 3
    assert [c.args[0] for c in sleeps.await_args_list] == [1.0, 2.0]  # exponential backoff


async def test_a_rejected_token_stops_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aiogram.exceptions import TelegramUnauthorizedError  # noqa: PLC0415
    from aiogram.methods import GetMe  # noqa: PLC0415

    monkeypatch.setattr(
        Dispatcher,
        "start_polling",
        AsyncMock(side_effect=TelegramUnauthorizedError(GetMe(), "Unauthorized")),
    )
    monkeypatch.setattr(Bot, "set_my_commands", AsyncMock())
    cfg = settings(f"sqlite:///{tmp_path / 'u.db'}")
    cfg = cfg.model_copy(update={"heartbeat_path": str(tmp_path / "hb")})
    async with engine_with_schema(cfg.database_url):
        with pytest.raises(SystemExit) as info:
            await app_main.run(cfg)
    assert info.value.code == app_main.EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "rejected BOT_TOKEN" in err
    assert TOKEN not in err


async def test_a_stop_signal_during_the_retry_wait_ends_the_process_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aiogram.exceptions import TelegramNetworkError  # noqa: PLC0415
    from aiogram.methods import GetMe  # noqa: PLC0415

    attempts = AsyncMock(side_effect=TelegramNetworkError(GetMe(), "down"))
    monkeypatch.setattr(Dispatcher, "start_polling", attempts)
    monkeypatch.setattr(Bot, "set_my_commands", AsyncMock())
    monkeypatch.setattr(app_main, "_sleep", AsyncMock(return_value=True))  # SIGTERM arrived
    cfg = settings(f"sqlite:///{tmp_path / 's.db'}")
    cfg = cfg.model_copy(update={"heartbeat_path": str(tmp_path / "hb")})
    async with engine_with_schema(cfg.database_url):
        await app_main.run(cfg)  # returns instead of retrying forever
    assert attempts.await_count == 1


async def test_the_real_retry_wait_reacts_to_sigterm() -> None:
    import os  # noqa: PLC0415
    import signal  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    loop.call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
    started = loop.time()
    assert await app_main._sleep(30.0) is True
    assert loop.time() - started < 5
    assert await app_main._sleep(0.01) is False
