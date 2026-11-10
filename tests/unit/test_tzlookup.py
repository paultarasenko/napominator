from __future__ import annotations

import pytest

from napominator.domain.timeutil import is_valid_timezone
from napominator.domain.timezones import POPULAR_ZONES, zone_label
from napominator.infrastructure.tzlookup import TimezoneResolver, normalize_city


@pytest.fixture(scope="module")
def resolver() -> TimezoneResolver:
    return TimezoneResolver()


@pytest.mark.parametrize(
    ("latitude", "longitude", "zone"),
    [
        (55.75, 37.62, "Europe/Moscow"),
        (56.84, 60.61, "Asia/Yekaterinburg"),
        (-33.87, 151.21, "Australia/Sydney"),
        (40.71, -74.0, "America/New_York"),
    ],
)
def test_location_to_zone(
    resolver: TimezoneResolver, latitude: float, longitude: float, zone: str
) -> None:
    assert resolver.from_location(latitude, longitude) == zone


@pytest.mark.parametrize(("latitude", "longitude"), [(91, 0), (0, 181), (-91, 0)])
def test_nonsense_coordinates_are_rejected(
    resolver: TimezoneResolver, latitude: float, longitude: float
) -> None:
    assert resolver.from_location(latitude, longitude) is None


@pytest.mark.parametrize(
    ("query", "zone"),
    [
        ("Москва", "Europe/Moscow"),
        ("москва", "Europe/Moscow"),
        ("  Санкт-Петербург ", "Europe/Moscow"),
        ("санкт петербург", "Europe/Moscow"),
        ("Екатеринбург", "Asia/Yekaterinburg"),
        ("Владивосток", "Asia/Vladivostok"),
        ("Алматы", "Asia/Almaty"),
        ("Нью-Йорк", "America/New_York"),
        ("Berlin", "Europe/Berlin"),
        ("Лондон", "Europe/London"),  # the biggest "London" wins over the Canadian one
    ],
)
def test_exact_city_names(resolver: TimezoneResolver, query: str, zone: str) -> None:
    match = resolver.from_city(query)
    assert match is not None
    assert match.timezone == zone


@pytest.mark.parametrize(
    ("typo", "zone"),
    [
        ("масква", "Europe/Moscow"),
        ("екатеренбург", "Asia/Yekaterinburg"),
        ("новосибирс", "Asia/Novosibirsk"),
        ("владевосток", "Asia/Vladivostok"),
    ],
)
def test_typos_are_tolerated(resolver: TimezoneResolver, typo: str, zone: str) -> None:
    match = resolver.from_city(typo)
    assert match is not None
    assert match.timezone == zone


@pytest.mark.parametrize("query", ["", "   ", "абвгдеёжзийклмн", "x" * 200, "12345", "🙂"])
def test_unknown_input_is_none(resolver: TimezoneResolver, query: str) -> None:
    assert resolver.from_city(query) is None


def test_normalisation() -> None:
    assert normalize_city("  Ёлки-Палки ") == "елки палки"


def test_every_curated_zone_is_a_valid_iana_name() -> None:
    names = [name for _, zones in POPULAR_ZONES for _, name in zones]
    assert len(names) == len(set(names))
    assert all(is_valid_timezone(n) for n in names)
    assert zone_label("Europe/Moscow") == "Москва"
    assert zone_label("Pacific/Fiji") == "Pacific/Fiji"
