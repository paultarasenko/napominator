from __future__ import annotations

import os
import time
from pathlib import Path

from napominator.infrastructure.heartbeat import FileHeartbeat, heartbeat_age


def test_beat_creates_the_file_and_age_is_small(tmp_path: Path) -> None:
    path = str(tmp_path / "sub" / "hb")
    assert heartbeat_age(path) is None
    FileHeartbeat(path)()
    age = heartbeat_age(path)
    assert age is not None
    assert age < 2.0


def test_age_grows_and_unreadable_files_count_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    path.write_text(str(time.time() - 120))
    age = heartbeat_age(str(path))
    assert age is not None
    assert age >= 120
    path.write_text("garbage")
    assert heartbeat_age(str(path)) is None
    os.remove(path)  # noqa: PTH107
    assert heartbeat_age(str(path)) is None
