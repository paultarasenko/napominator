"""Fuzzy search over a user's own reminders (spec S8)."""

from __future__ import annotations

from rapidfuzz import fuzz, process

from napominator.domain.entities import Reminder

MIN_SCORE = 62.0
MAX_RESULTS = 8
MAX_QUERY = 100


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def find(reminders: list[Reminder], query: str, limit: int = MAX_RESULTS) -> list[Reminder]:
    """Best matches first; tolerant to typos and word forms (``хлеб`` finds ``купить хлеба``)."""
    needle = _norm(query)[:MAX_QUERY]
    if not needle:
        return []
    haystack = {r.id: _norm(r.text) for r in reminders}
    hits = process.extract(
        needle, haystack, scorer=fuzz.partial_ratio, score_cutoff=MIN_SCORE, limit=limit * 3
    )
    by_id = {r.id: r for r in reminders}
    ranked = sorted(hits, key=lambda h: (-h[1], by_id[h[2]].id))
    return [by_id[h[2]] for h in ranked[:limit]]
