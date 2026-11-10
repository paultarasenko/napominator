from datetime import UTC, datetime, timedelta

import pytest

from napominator.application.ports import Clock
from napominator.infrastructure.clock import SystemClock
from tests.support import FakeClock


def test_system_clock_is_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=1)


def test_fake_clock_moves_only_on_demand() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    assert clock.now() == clock.now()
    clock.advance(minutes=5)
    assert clock.now() == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    clock.set(datetime(2030, 1, 1, tzinfo=UTC))
    assert clock.now().year == 2030


def test_fake_clock_rejects_naive() -> None:
    with pytest.raises(ValueError, match="aware"):
        FakeClock(datetime(2026, 1, 1))


def test_clocks_satisfy_the_port() -> None:
    clocks: list[Clock] = [SystemClock(), FakeClock()]
    assert all(c.now().tzinfo is not None for c in clocks)
