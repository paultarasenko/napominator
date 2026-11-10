"""Category resolution and hints. Pure functions; no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from rapidfuzz import fuzz, process

from napominator.nlp.lexicon import lemmas

FUZZY_CUTOFF = 82.0
_WORD = re.compile(r"[^\W\d_]{3,}")

# lemma -> default category name (only matched against the user's existing categories).
DEFAULT_HINTS: dict[str, str] = {
    "врач": "Здоровье",
    "таблетка": "Здоровье",
    "лекарство": "Здоровье",
    "анализ": "Здоровье",
    "стоматолог": "Здоровье",
    "аренда": "Финансы",
    "оплатить": "Финансы",
    "кредит": "Финансы",
    "налог": "Финансы",
    "счет": "Финансы",
    "купить": "Покупки",
    "магазин": "Покупки",
    "молоко": "Покупки",
    "хлеб": "Покупки",
    "планерка": "Работа",
    "совещание": "Работа",
    "отчет": "Работа",
    "созвон": "Работа",
    "встреча": "Работа",
    "клиент": "Работа",
    "день": "Даты",
    "рождение": "Даты",
    "годовщина": "Даты",
    "уборка": "Дом",
    "стирка": "Дом",
    "квартира": "Дом",
}


def match_existing(ref: str, names: Iterable[str]) -> str | None:
    """Best fuzzy match of an explicit reference among existing category names."""
    choices = {name: name.casefold() for name in names}
    if not choices or not ref.strip():
        return None
    hit = process.extractOne(ref.casefold(), choices, scorer=fuzz.WRatio, score_cutoff=FUZZY_CUTOFF)
    return None if hit is None else str(hit[2])


def significant_lemmas(text: str) -> list[str]:
    """Lower-cased content lemmas of ``text`` (used for learning and dictionary hints)."""
    found: list[str] = []
    for word in _WORD.findall(text.casefold().replace("ё", "е")):
        options = sorted(lemmas(word))
        found.append(options[0] if options else word)
    return found


def suggest(
    text: str,
    names: Iterable[str],
    learned: Mapping[str, Mapping[str, int]] | None = None,
) -> str | None:
    """Suggest an existing category: learned history first, then the built-in dictionary."""
    available = {name.casefold(): name for name in names}
    if not available:
        return None
    words = significant_lemmas(text)
    scores: dict[str, int] = {}
    for word in words:
        for category, count in (learned or {}).get(word, {}).items():
            if category.casefold() in available:
                scores[category] = scores.get(category, 0) + count
    if scores:
        return available[max(scores, key=lambda c: scores[c]).casefold()]
    for word in words:
        hint = DEFAULT_HINTS.get(word)
        if hint and hint.casefold() in available:
            return available[hint.casefold()]
    return None
