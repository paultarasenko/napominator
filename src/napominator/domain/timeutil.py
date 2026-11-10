"""Wall-clock <-> UTC conversion with the DST policy from ADR-0006.

* Non-existent local time (spring-forward gap) -> the first valid instant after the jump.
* Ambiguous local time (fall-back overlap)      -> the first occurrence (``fold=0``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["is_valid_timezone", "load_zone", "local_to_utc", "utc_to_local"]


def load_zone(name: str) -> ZoneInfo:
    """Return the IANA zone or raise ``ValueError`` (never a low-level lookup error)."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        msg = f"unknown timezone: {name!r}"
        raise ValueError(msg) from exc


def is_valid_timezone(name: str) -> bool:
    try:
        load_zone(name)
    except ValueError:
        return False
    return True


def utc_to_local(moment: datetime, tz: tzinfo) -> datetime:
    """Aware UTC -> naive local wall-clock time (the ``*_local`` representation)."""
    _require_aware(moment)
    return moment.astimezone(tz).replace(tzinfo=None)


def local_to_utc(local: datetime, tz: tzinfo) -> datetime:
    """Naive local wall-clock time -> aware UTC, applying the DST policy."""
    if local.tzinfo is not None:
        msg = "local_to_utc expects a naive local datetime"
        raise ValueError(msg)
    first = local.replace(tzinfo=tz, fold=0)
    as_utc = first.astimezone(UTC)
    if as_utc.astimezone(tz).replace(tzinfo=None) == local:
        return as_utc  # regular or ambiguous (fold=0 => first occurrence)
    return _first_instant_after_gap(local, tz)


def _first_instant_after_gap(local: datetime, tz: tzinfo) -> datetime:
    """``local`` falls into a gap: return the UTC instant of the transition itself.

    ``before``/``after`` are the offsets on either side of the jump; the transition lies
    in ``(lo, hi]`` and is found by bisection (transitions happen on whole minutes).
    """
    before = local.replace(tzinfo=tz, fold=0).utcoffset()
    after = local.replace(tzinfo=tz, fold=1).utcoffset()
    if before is None or after is None:  # pragma: no cover - zoneinfo always has offsets
        msg = "timezone without UTC offset"
        raise ValueError(msg)
    naive_utc = local.replace(tzinfo=UTC)
    lo = naive_utc - after  # local time is still before the jump
    hi = naive_utc - before  # already after the jump
    step = timedelta(minutes=1)
    while hi - lo > step:
        mid = lo + (hi - lo) // 2
        if mid.astimezone(tz).utcoffset() == after:
            hi = mid
        else:
            lo = mid
    return hi


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = "expected an aware datetime"
        raise ValueError(msg)
