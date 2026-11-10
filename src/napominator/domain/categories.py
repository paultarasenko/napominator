"""Category rules: naming, emoji extraction, defaults. Pure functions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from napominator.domain.entities import MAX_CATEGORY_NAME

DEFAULT_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("🏠", "Дом"),
    ("💼", "Работа"),
    ("💊", "Здоровье"),
    ("💰", "Финансы"),
    ("🛒", "Покупки"),
    ("🎂", "Даты"),
)
FALLBACK_EMOJI = "🗂"

_KEYWORD_EMOJI: dict[str, str] = {
    "дом": "🏠",
    "семья": "👨‍👩‍👧",
    "работа": "💼",
    "учеба": "🎓",
    "учёба": "🎓",
    "здоровье": "💊",
    "спорт": "🏃",
    "финансы": "💰",
    "деньги": "💰",
    "покупки": "🛒",
    "даты": "🎂",
    "праздники": "🎉",
    "путешествия": "✈️",
    "авто": "🚗",
    "машина": "🚗",
    "дети": "🧒",
    "друзья": "🧑‍🤝‍🧑",
    "хобби": "🎨",
    "питомцы": "🐾",
    "дача": "🌱",
}


class InvalidCategoryNameError(ValueError):
    """Empty, too long, or otherwise unusable category name."""


@dataclass(frozen=True, slots=True)
class CategoryInput:
    emoji: str | None
    name: str


def name_key(name: str) -> str:
    """Case-insensitive identity of a category name (works for Cyrillic, unlike SQL lower())."""
    return " ".join(name.split()).casefold()


_EMOJI_PLANES_FROM = 0x1F000
_DINGBATS = range(0x2600, 0x27C0)


def _is_emoji_char(ch: str) -> bool:
    if ch in {"\ufe0f", "\u200d"}:
        return True
    code = ord(ch)
    return (
        code >= _EMOJI_PLANES_FROM or unicodedata.category(ch) in {"So", "Sk"} or code in _DINGBATS
    )


def split_emoji(text: str) -> tuple[str | None, str]:
    """``"💼 Работа"`` -> (``"💼"``, ``"Работа"``). A leading symbol run counts as the emoji."""
    stripped = text.strip()
    i = 0
    while i < len(stripped) and _is_emoji_char(stripped[i]):
        i += 1
    if i == 0:
        return None, stripped
    return stripped[:i], stripped[i:].strip()


def parse_category_input(text: str) -> CategoryInput:
    """Validate user input for a category; emoji comes from the text or from keywords."""
    emoji, name = split_emoji(text)
    name = " ".join(name.split())
    if not name:
        raise InvalidCategoryNameError("empty")
    if len(name) > MAX_CATEGORY_NAME:
        raise InvalidCategoryNameError("too_long")
    if any(unicodedata.category(c).startswith("C") for c in name):
        raise InvalidCategoryNameError("bad_characters")
    return CategoryInput(emoji or guess_emoji(name), name)


def guess_emoji(name: str) -> str:
    key = name_key(name)
    for keyword, emoji in _KEYWORD_EMOJI.items():
        if key == keyword or key.startswith(keyword):
            return emoji
    return FALLBACK_EMOJI
