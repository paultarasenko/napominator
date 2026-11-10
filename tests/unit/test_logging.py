import json

import pytest

from napominator.infrastructure.logging import configure_logging, get_logger


def test_json_logging_emits_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    get_logger("t").info("hello", user_id=7)
    line = capsys.readouterr().err.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["event"] == "hello"
    assert data["user_id"] == 7
    assert data["level"] == "info"


def test_level_filters_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", "json")
    get_logger("t").info("hidden")
    assert "hidden" not in capsys.readouterr().err


def test_console_format_runs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "console")
    get_logger("t").info("visible")
    assert "visible" in capsys.readouterr().err
