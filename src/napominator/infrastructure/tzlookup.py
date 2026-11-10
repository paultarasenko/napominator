"""Offline time-zone detection (ADR-0008): coordinates -> zone, city name -> zone.

Heavy data is loaded lazily on first use and kept for the life of the process (about 120 MB
of RSS with the city index); call the methods through ``asyncio.to_thread``.
Coordinates are never stored or logged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property

from rapidfuzz import fuzz, process

from napominator.domain.timeutil import is_valid_timezone

_NAME = re.compile(r"^[а-яa-z][а-яa-z .'-]{1,40}$")
_FUZZY_CUTOFF = 84.0
_MAX_LATITUDE = 90
_MAX_LONGITUDE = 180
_MAX_QUERY = 60


@dataclass(frozen=True, slots=True)
class CityMatch:
    name: str  # as in the data set (English)
    timezone: str
    population: int


def normalize_city(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").replace("-", " ").split())


class TimezoneResolver:
    def __init__(self, min_population: int = 15_000) -> None:
        self._min_population = min_population

    @cached_property
    def _finder(self):  # type: ignore[no-untyped-def]
        from timezonefinder import TimezoneFinder  # noqa: PLC0415 - heavy, loaded on demand

        return TimezoneFinder()

    @cached_property
    def _index(self) -> dict[str, list[CityMatch]]:
        import geonamescache  # noqa: PLC0415 - heavy, loaded on demand

        cities = geonamescache.GeonamesCache(min_city_population=self._min_population)
        index: dict[str, list[CityMatch]] = {}
        for city in cities.get_cities().values():
            match = CityMatch(city["name"], city["timezone"], int(city["population"]))
            if not is_valid_timezone(match.timezone):
                continue
            for raw in {city["name"], *city["alternatenames"]}:
                key = normalize_city(raw)
                if _NAME.match(key):
                    index.setdefault(key, []).append(match)
        return index

    @cached_property
    def _cyrillic_keys(self) -> list[str]:
        return [k for k in self._index if "а" <= k[0] <= "я"]

    def from_location(self, latitude: float, longitude: float) -> str | None:
        if not (abs(latitude) <= _MAX_LATITUDE and abs(longitude) <= _MAX_LONGITUDE):
            return None
        zone = self._finder.timezone_at(lat=latitude, lng=longitude)
        return zone if zone and is_valid_timezone(zone) else None

    def from_city(self, query: str) -> CityMatch | None:
        """Best match for a city name (exact first, then typo-tolerant); biggest city wins."""
        key = normalize_city(query)
        if not key or len(key) > _MAX_QUERY:
            return None
        exact = self._index.get(key)
        if exact:
            return max(exact, key=lambda m: m.population)
        pool = self._cyrillic_keys if "а" <= key[0] <= "я" else list(self._index)
        hit = process.extractOne(key, pool, scorer=fuzz.WRatio, score_cutoff=_FUZZY_CUTOFF)
        if hit is None:
            return None
        return max(self._index[hit[0]], key=lambda m: m.population)
