from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from napominator.domain.timeutil import is_valid_timezone, load_zone, local_to_utc, utc_to_local

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")


def test_regular_conversion() -> None:
    moscow = ZoneInfo("Europe/Moscow")
    assert local_to_utc(datetime(2026, 10, 13, 9, 0), moscow) == datetime(
        2026, 10, 13, 6, 0, tzinfo=UTC
    )
    assert utc_to_local(datetime(2026, 10, 13, 6, 0, tzinfo=UTC), moscow) == datetime(
        2026, 10, 13, 9, 0
    )


@pytest.mark.parametrize(
    ("zone", "skipped", "expected_local"),
    [
        (BERLIN, datetime(2026, 3, 29, 2, 30), datetime(2026, 3, 29, 3, 0)),
        (NEW_YORK, datetime(2026, 3, 8, 2, 30), datetime(2026, 3, 8, 3, 0)),
        (LORD_HOWE, datetime(2026, 10, 4, 2, 15), datetime(2026, 10, 4, 2, 30)),
    ],
)
def test_nonexistent_time_fires_at_first_valid_instant_after_the_jump(
    zone: ZoneInfo, skipped: datetime, expected_local: datetime
) -> None:
    result = local_to_utc(skipped, zone)
    assert utc_to_local(result, zone) == expected_local


def test_gap_boundary_is_exact() -> None:
    # 02:00 itself does not exist in Berlin on 2026-03-29; 03:00 CEST is the same instant.
    assert local_to_utc(datetime(2026, 3, 29, 2, 0), BERLIN) == local_to_utc(
        datetime(2026, 3, 29, 3, 0), BERLIN
    )


@pytest.mark.parametrize(
    ("zone", "ambiguous"),
    [
        (BERLIN, datetime(2026, 10, 25, 2, 30)),
        (NEW_YORK, datetime(2026, 11, 1, 1, 30)),
        (LORD_HOWE, datetime(2026, 4, 5, 1, 45)),
    ],
)
def test_ambiguous_time_uses_first_occurrence(zone: ZoneInfo, ambiguous: datetime) -> None:
    first = local_to_utc(ambiguous, zone)
    second = ambiguous.replace(tzinfo=zone, fold=1).astimezone(UTC)
    assert first < second
    assert utc_to_local(first, zone) == ambiguous


def test_local_to_utc_rejects_aware_input() -> None:
    with pytest.raises(ValueError, match="naive"):
        local_to_utc(datetime(2026, 1, 1, tzinfo=UTC), BERLIN)


def test_utc_to_local_rejects_naive_input() -> None:
    with pytest.raises(ValueError, match="aware"):
        utc_to_local(datetime(2026, 1, 1), BERLIN)


def test_zone_helpers() -> None:
    assert load_zone("Europe/Riga").key == "Europe/Riga"
    assert is_valid_timezone("Europe/Riga")
    assert not is_valid_timezone("Mars/Olympus")
    assert not is_valid_timezone("")
    with pytest.raises(ValueError, match="unknown timezone"):
        load_zone("Mars/Olympus")
