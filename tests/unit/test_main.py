from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from napominator import main as app_main


def _run(env_extra: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"BOT_TOKEN", "DATABASE_URL"}}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "napominator"],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
        check=False,
    )


def test_exits_clearly_without_token(tmp_path: Path) -> None:
    result = _run({}, tmp_path)
    assert result.returncode == app_main.EXIT_CONFIG_ERROR
    assert "BOT_TOKEN" in result.stderr
    assert "@BotFather" in result.stderr
    assert "Traceback" not in result.stderr


def test_exits_clearly_on_invalid_token_format(tmp_path: Path) -> None:
    result = _run(
        {"BOT_TOKEN": "not-a-valid-token", "DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}"},
        tmp_path,
    )
    assert result.returncode == app_main.EXIT_CONFIG_ERROR
    assert "invalid format" in result.stderr
    assert "Traceback" not in result.stderr


def test_load_settings_reports_each_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as info:
        app_main.load_settings()
    assert info.value.code == app_main.EXIT_CONFIG_ERROR
    assert "BOT_TOKEN" in capsys.readouterr().err
