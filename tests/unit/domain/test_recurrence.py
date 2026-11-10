from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from napominator.domain.recurrence import (
    Frequency,
    InvalidRecurrenceError,
    Recurrence,
    Schedule,
    next_occurrence,
    occurrences,
)
from napominator.domain.timeutil import local_to_utc, utc_to_local

MOSCOW = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def local_series(schedule: Schedule, after: datetime, n: int) -> list[datetime]:
    return [utc_to_local(m, schedule.tz) for m in occurrences(schedule, after, n)]


# --- one-off ---


def test_one_off_in_future_and_past() -> None:
    sched = Schedule(datetime(2026, 10, 14, 18, 0), MOSCOW)
    assert next_occurrence(sched, utc(2026, 10, 13, 12, 0)) == utc(2026, 10, 14, 15, 0)
    assert next_occurrence(sched, utc(2026, 10, 14, 15, 0)) is None  # strictly after
    assert next_occurrence(sched, utc(2026, 10, 15)) is None


def test_after_must_be_aware() -> None:
    with pytest.raises(ValueError, match="aware"):
        next_occurrence(Schedule(datetime(2026, 1, 1), MOSCOW), datetime(2026, 1, 1))


def test_schedule_rejects_aware_dtstart() -> None:
    with pytest.raises(InvalidRecurrenceError):
        Schedule(datetime(2026, 1, 1, tzinfo=UTC), MOSCOW)


# --- daily / weekly ---


def test_daily_every_three_days() -> None:
    sched = Schedule(datetime(2026, 10, 14, 10, 0), MOSCOW, Recurrence(Frequency.DAILY, 3))
    assert local_series(sched, utc(2026, 10, 13), 3) == [
        datetime(2026, 10, 14, 10, 0),
        datetime(2026, 10, 17, 10, 0),
        datetime(2026, 10, 20, 10, 0),
    ]


def test_weekly_on_weekdays() -> None:
    rule = Recurrence(Frequency.WEEKLY, byweekday=(0, 1, 2, 3, 4))
    sched = Schedule(datetime(2026, 10, 14, 8, 30), MOSCOW, rule)  # Wednesday
    days = [d.strftime("%a %d") for d in local_series(sched, utc(2026, 10, 13), 5)]
    assert days == ["Wed 14", "Thu 15", "Fri 16", "Mon 19", "Tue 20"]


def test_weekly_defaults_to_dtstart_weekday_and_interval() -> None:
    sched = Schedule(datetime(2026, 10, 19, 9, 0), MOSCOW, Recurrence(Frequency.WEEKLY, 2))
    assert local_series(sched, utc(2026, 10, 13), 3) == [
        datetime(2026, 10, 19, 9, 0),
        datetime(2026, 11, 2, 9, 0),
        datetime(2026, 11, 16, 9, 0),
    ]


def test_count_and_until_end_the_series() -> None:
    counted = Schedule(datetime(2026, 1, 1, 9), MOSCOW, Recurrence(Frequency.DAILY, count=3))
    assert len(occurrences(counted, utc(2025, 1, 1), 10)) == 3
    assert next_occurrence(counted, utc(2026, 1, 3, 6, 0)) is None
    bounded = Schedule(
        datetime(2026, 1, 1, 9),
        MOSCOW,
        Recurrence(Frequency.DAILY, until_local=datetime(2026, 1, 3, 9, 0)),
    )
    assert len(occurrences(bounded, utc(2025, 1, 1), 10)) == 3  # UNTIL is inclusive


# --- monthly / yearly clamping ---


def test_every_31st_clamps_to_last_day_of_short_months() -> None:
    sched = Schedule(
        datetime(2026, 1, 31, 9, 0), MOSCOW, Recurrence(Frequency.MONTHLY, bymonthday=(31,))
    )
    days = [d.date().isoformat() for d in local_series(sched, utc(2026, 1, 1), 6)]
    assert days == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
        "2026-05-31",
        "2026-06-30",
    ]


def test_monthly_without_bymonthday_uses_dtstart_day_and_leap_february() -> None:
    sched = Schedule(datetime(2027, 12, 30, 9, 0), MOSCOW, Recurrence(Frequency.MONTHLY))
    days = [d.date().isoformat() for d in local_series(sched, utc(2027, 1, 1), 3)]
    assert days == ["2027-12-30", "2028-01-30", "2028-02-29"]


def test_last_day_of_month() -> None:
    sched = Schedule(
        datetime(2026, 1, 31, 18, 0), MOSCOW, Recurrence(Frequency.MONTHLY, bymonthday=(-1,))
    )
    days = [d.day for d in local_series(sched, utc(2026, 1, 1), 4)]
    assert days == [31, 28, 31, 30]


def test_yearly_feb_29_falls_back_to_feb_28_in_common_years() -> None:
    sched = Schedule(datetime(2028, 2, 29, 9, 0), MOSCOW, Recurrence(Frequency.YEARLY))
    days = [d.date().isoformat() for d in local_series(sched, utc(2028, 1, 1), 6)]
    assert days == [
        "2028-02-29",
        "2029-02-28",
        "2030-02-28",
        "2031-02-28",
        "2032-02-29",
        "2033-02-28",
    ]


def test_yearly_birthday() -> None:
    sched = Schedule(
        datetime(2027, 5, 15, 9, 0), MOSCOW, Recurrence(Frequency.YEARLY, bymonth=(5,))
    )
    assert local_series(sched, utc(2026, 10, 13), 2) == [
        datetime(2027, 5, 15, 9, 0),
        datetime(2028, 5, 15, 9, 0),
    ]


def test_monthly_count_and_until() -> None:
    counted = Schedule(datetime(2026, 1, 15, 9), MOSCOW, Recurrence(Frequency.MONTHLY, count=2))
    assert len(occurrences(counted, utc(2025, 1, 1), 9)) == 2
    bounded = Schedule(
        datetime(2026, 1, 15, 9),
        MOSCOW,
        Recurrence(Frequency.MONTHLY, until_local=datetime(2026, 3, 15, 9, 0)),
    )
    assert len(occurrences(bounded, utc(2025, 1, 1), 9)) == 3
    yearly = Schedule(datetime(2026, 5, 1, 9), MOSCOW, Recurrence(Frequency.YEARLY, count=2))
    assert len(occurrences(yearly, utc(2025, 1, 1), 9)) == 2


# --- sub-daily ---


def test_every_two_hours_is_absolute_time() -> None:
    sched = Schedule(datetime(2026, 10, 13, 15, 0), MOSCOW, Recurrence(Frequency.HOURLY, 2))
    got = occurrences(sched, utc(2026, 10, 13, 12, 0), 3)  # dtstart itself is 12:00 UTC
    assert got == [utc(2026, 10, 13, 14, 0), utc(2026, 10, 13, 16, 0), utc(2026, 10, 13, 18, 0)]


def test_hourly_steps_are_absolute_across_a_dst_change() -> None:
    sched = Schedule(datetime(2026, 3, 29, 0, 0), BERLIN, Recurrence(Frequency.HOURLY, 1))
    moments = occurrences(sched, utc(2026, 3, 28, 22, 0), 4)  # 23:00Z, 00:00Z, 01:00Z, 02:00Z
    assert [b - a for a, b in itertools.pairwise(moments)] == [timedelta(hours=1)] * 3


def test_sub_daily_count_until_and_minimum_interval() -> None:
    counted = Schedule(datetime(2026, 1, 1, 9), MOSCOW, Recurrence(Frequency.HOURLY, count=2))
    assert len(occurrences(counted, utc(2025, 1, 1), 9)) == 2
    bounded = Schedule(
        datetime(2026, 1, 1, 9),
        MOSCOW,
        Recurrence(Frequency.MINUTELY, 10, until_local=datetime(2026, 1, 1, 9, 30)),
    )
    assert len(occurrences(bounded, utc(2025, 1, 1), 99)) == 4
    with pytest.raises(InvalidRecurrenceError, match="5 minutes"):
        Recurrence(Frequency.MINUTELY, 4)
    Recurrence(Frequency.MINUTELY, 5)  # exactly the minimum is fine


# --- horizon ---


def test_horizon_is_ten_years() -> None:
    far = Schedule(datetime(2040, 1, 1, 9), MOSCOW)
    assert next_occurrence(far, utc(2026, 10, 13)) is None
    near = Schedule(datetime(2036, 10, 1, 9), MOSCOW)
    assert next_occurrence(near, utc(2026, 10, 13)) is not None


# --- DST ---


@pytest.mark.parametrize(
    ("zone", "start", "after"),
    [
        (BERLIN, datetime(2026, 3, 27, 9, 0), utc(2026, 3, 26)),
        (NEW_YORK, datetime(2026, 3, 6, 9, 0), utc(2026, 3, 5)),
        (LORD_HOWE, datetime(2026, 10, 2, 9, 0), utc(2026, 10, 1)),
        (BERLIN, datetime(2026, 10, 23, 9, 0), utc(2026, 10, 22)),
    ],
)
def test_daily_wall_clock_time_survives_dst_changes(
    zone: ZoneInfo, start: datetime, after: datetime
) -> None:
    sched = Schedule(start, zone, Recurrence(Frequency.DAILY))
    series = local_series(sched, after, 6)
    assert [d.time() for d in series] == [start.time()] * 6


def test_daily_in_the_gap_fires_once_per_day_and_monotonically() -> None:
    sched = Schedule(datetime(2026, 3, 28, 2, 30), BERLIN, Recurrence(Frequency.DAILY))
    moments = occurrences(sched, utc(2026, 3, 27), 4)
    assert moments == sorted(set(moments))
    assert [utc_to_local(m, BERLIN) for m in moments] == [
        datetime(2026, 3, 28, 2, 30),
        datetime(2026, 3, 29, 3, 0),  # 02:30 does not exist -> first valid instant
        datetime(2026, 3, 30, 2, 30),
        datetime(2026, 3, 31, 2, 30),
    ]


def test_daily_in_the_overlap_fires_once() -> None:
    sched = Schedule(datetime(2026, 10, 24, 2, 30), BERLIN, Recurrence(Frequency.DAILY))
    moments = occurrences(sched, utc(2026, 10, 23), 3)
    assert len(moments) == len(set(moments)) == 3
    assert moments[1] == utc(2026, 10, 25, 0, 30)  # first occurrence of the repeated hour


# --- value object ---


@pytest.mark.parametrize(
    "rule",
    [
        Recurrence(Frequency.DAILY),
        Recurrence(Frequency.DAILY, 3),
        Recurrence(Frequency.WEEKLY, byweekday=(1, 3)),
        Recurrence(Frequency.MONTHLY, bymonthday=(1, 15), count=4),
        Recurrence(Frequency.MONTHLY, bymonthday=(-1,)),
        Recurrence(Frequency.YEARLY, bymonth=(5,), until_local=datetime(2030, 1, 1, 23, 59, 59)),
        Recurrence(Frequency.HOURLY, 2),
    ],
)
def test_rrule_roundtrip(rule: Recurrence) -> None:
    assert Recurrence.from_rrule(rule.to_rrule()) == rule


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "FREQ=SECONDLY",
        "FREQ=DAILY;FOO=1",
        "FREQ=DAILY;INTERVAL=x",
        "INTERVAL=2",
        "FREQ=DAILY;BYDAY=XX",
    ],
)
def test_from_rrule_rejects_garbage(bad: str) -> None:
    with pytest.raises(InvalidRecurrenceError):
        Recurrence.from_rrule(bad)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"freq": Frequency.DAILY, "interval": 0},
        {"freq": Frequency.DAILY, "count": 0},
        {"freq": Frequency.DAILY, "byweekday": (1,)},
        {"freq": Frequency.WEEKLY, "bymonthday": (1,)},
        {"freq": Frequency.DAILY, "bymonth": (1,)},
        {"freq": Frequency.WEEKLY, "byweekday": (7,)},
        {"freq": Frequency.MONTHLY, "bymonthday": (0,)},
        {"freq": Frequency.MONTHLY, "bymonthday": (32,)},
        {"freq": Frequency.YEARLY, "bymonth": (13,)},
        {"freq": Frequency.YEARLY, "bymonth": (1, 2)},
        {"freq": Frequency.DAILY, "until_local": datetime(2026, 1, 1, tzinfo=UTC)},
    ],
)
def test_invalid_recurrences_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(InvalidRecurrenceError):
        Recurrence(**kwargs)  # type: ignore[arg-type]


# --- properties ---

zones = st.sampled_from([MOSCOW, BERLIN, NEW_YORK, LORD_HOWE])
local_starts = st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2030, 12, 31))
rules = st.one_of(
    st.builds(Recurrence, freq=st.just(Frequency.DAILY), interval=st.integers(1, 5)),
    st.builds(
        Recurrence,
        freq=st.just(Frequency.WEEKLY),
        interval=st.integers(1, 3),
        byweekday=st.lists(st.integers(0, 6), min_size=1, max_size=4, unique=True).map(tuple),
    ),
    st.builds(
        Recurrence,
        freq=st.just(Frequency.MONTHLY),
        interval=st.integers(1, 3),
        bymonthday=st.lists(st.sampled_from([1, 15, 29, 30, 31, -1]), min_size=1, max_size=3).map(
            lambda xs: tuple(sorted(set(xs)))
        ),
    ),
    st.builds(Recurrence, freq=st.just(Frequency.YEARLY), interval=st.integers(1, 2)),
    st.builds(Recurrence, freq=st.just(Frequency.HOURLY), interval=st.integers(1, 12)),
    st.builds(Recurrence, freq=st.just(Frequency.MINUTELY), interval=st.integers(5, 90)),
)


@settings(max_examples=150, deadline=None)
@given(zone=zones, start=local_starts, rule=rules, offset_days=st.integers(0, 400))
def test_series_is_strictly_monotonic_and_after_the_cursor(
    zone: ZoneInfo, start: datetime, rule: Recurrence, offset_days: int
) -> None:
    sched = Schedule(start.replace(second=0, microsecond=0), zone, rule)
    after = utc(2026, 1, 1) + timedelta(days=offset_days)
    series = occurrences(sched, after, 8)
    assert all(m > after for m in series)
    assert all(a < b for a, b in itertools.pairwise(series))


@settings(max_examples=150, deadline=None)
@given(zone=zones, start=local_starts, interval=st.integers(1, 4))
def test_daily_keeps_wall_clock_time_except_when_that_time_does_not_exist(
    zone: ZoneInfo, start: datetime, interval: int
) -> None:
    start = start.replace(second=0, microsecond=0)
    sched = Schedule(start, zone, Recurrence(Frequency.DAILY, interval))
    for moment in occurrences(sched, utc(2025, 12, 31), 6):
        local = utc_to_local(moment, zone)
        if local.time() != start.time():
            wanted = datetime.combine(local.date(), start.time())
            assert utc_to_local(local_to_utc(wanted, zone), zone) != wanted  # it was in a gap


@settings(max_examples=100, deadline=None)
@given(
    zone=zones,
    start=local_starts,
    count=st.integers(1, 6),
)
def test_count_is_respected(zone: ZoneInfo, start: datetime, count: int) -> None:
    sched = Schedule(start, zone, Recurrence(Frequency.DAILY, count=count))
    assert len(occurrences(sched, utc(2025, 12, 31), 50)) == count


@settings(max_examples=100, deadline=None)
@given(zone=zones, start=local_starts, days=st.integers(0, 30))
def test_until_is_respected(zone: ZoneInfo, start: datetime, days: int) -> None:
    until = start + timedelta(days=days)
    sched = Schedule(start, zone, Recurrence(Frequency.DAILY, until_local=until))
    for moment in occurrences(sched, utc(2025, 12, 31), 50):
        assert utc_to_local(moment, zone) <= until + timedelta(hours=1)
