from __future__ import annotations

import subprocess
import sys


def test_sandbox_prints_structured_result() -> None:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "napominator.nlp",
            "завтра в 9 утра позвонить маме",
            "--now",
            "2026-10-13 15:00",
            "--tz",
            "Europe/Moscow",
            "--category",
            "Дом",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    assert "2026-10-14 09:00:00" in out
    assert "'Позвонить маме'" in out
    assert "clarify=False" in out
