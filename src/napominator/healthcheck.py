"""Container HEALTHCHECK: the delivery loop is alive and the database answers.

Exit code 0 = healthy, 1 = unhealthy (the reasons go to stderr, never any secrets).
"""

from __future__ import annotations

import asyncio
import sys

from napominator.infrastructure.config import Settings
from napominator.infrastructure.db.engine import create_engine, ping
from napominator.infrastructure.heartbeat import heartbeat_age

MAX_HEARTBEAT_AGE = 60.0  # seconds


async def problems(settings: Settings, *, max_age: float = MAX_HEARTBEAT_AGE) -> list[str]:
    found: list[str] = []
    age = heartbeat_age(settings.heartbeat_path)
    if age is None:
        found.append("no heartbeat yet")
    elif age > max_age:
        found.append(f"heartbeat is {int(age)} s old (limit {int(max_age)} s)")
    engine = create_engine(settings.database_url)
    try:
        await asyncio.wait_for(ping(engine), timeout=5)
    except Exception as exc:  # any driver error means "unhealthy", whatever it is
        found.append(f"database unreachable ({type(exc).__name__})")
    finally:
        await engine.dispose()
    return found


def main() -> None:
    try:
        settings = Settings()
    except Exception:
        print("unhealthy: invalid configuration", file=sys.stderr)  # noqa: T201
        raise SystemExit(1) from None
    issues = asyncio.run(problems(settings))
    if issues:
        print("unhealthy: " + "; ".join(issues), file=sys.stderr)  # noqa: T201
        raise SystemExit(1)


if __name__ == "__main__":
    main()
