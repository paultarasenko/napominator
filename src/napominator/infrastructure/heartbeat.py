"""File-based heartbeat for the container HEALTHCHECK."""

from __future__ import annotations

import time
from pathlib import Path


class FileHeartbeat:
    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def __call__(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(str(time.time()))
        tmp.replace(self._path)  # atomic: a reader never sees a half-written file


def heartbeat_age(path: str) -> float | None:
    """Seconds since the last beat, or ``None`` if there is none (or it is unreadable)."""
    try:
        return max(time.time() - float(Path(path).read_text()), 0.0)
    except (OSError, ValueError):
        return None
