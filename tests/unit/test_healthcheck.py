from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from napominator import healthcheck
from napominator.infrastructure.config import Settings
from napominator.infrastructure.heartbeat import FileHeartbeat

TOKEN = "123456:ABCDEF-token-for-tests"


def settings(tmp_path: Path, db: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        bot_token=TOKEN,
        database_url=db or f"sqlite:///{tmp_path / 'h.db'}",
        heartbeat_path=str(tmp_path / "hb"),
    )


async def test_healthy_with_a_fresh_heartbeat_and_a_reachable_database(tmp_path: Path) -> None:
    FileHeartbeat(str(tmp_path / "hb"))()
    assert await healthcheck.problems(settings(tmp_path)) == []


async def test_a_missing_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    assert await healthcheck.problems(settings(tmp_path)) == ["no heartbeat yet"]


async def test_a_stale_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    (tmp_path / "hb").write_text(str(time.time() - 300))
    issues = await healthcheck.problems(settings(tmp_path))
    assert len(issues) == 1
    assert "heartbeat is" in issues[0]


async def test_an_unreachable_database_is_unhealthy_even_with_a_fresh_heartbeat(
    tmp_path: Path,
) -> None:
    FileHeartbeat(str(tmp_path / "hb"))()
    broken = settings(tmp_path, f"sqlite:///{tmp_path / 'no-such-dir' / 'x.db'}")
    issues = await healthcheck.problems(broken)
    assert len(issues) == 1
    assert "database unreachable" in issues[0]
    assert TOKEN not in issues[0]


def test_the_cli_exits_with_1_and_explains(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "napominator.healthcheck"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "BOT_TOKEN": TOKEN,
            "DATABASE_URL": f"sqlite:///{tmp_path / 'c.db'}",
            "HEARTBEAT_PATH": str(tmp_path / "hb"),
        },
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "no heartbeat yet" in result.stderr
    assert TOKEN not in result.stderr


def test_the_cli_rejects_a_broken_configuration(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "napominator.healthcheck"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "invalid configuration" in result.stderr
