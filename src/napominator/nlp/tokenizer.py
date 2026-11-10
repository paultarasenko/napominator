"""Normalisation and tokenisation with character positions.

Invariant: ``len(norm) == len(base)`` so every span found on the normalised text maps
one-to-one onto the user's own text (which is what we keep as the reminder body).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from napominator.nlp.models import MAX_INPUT_LENGTH

_TIME_PREPOSITIONS = re.compile(r"(?:^|\s)(?:в|во|к|после|около)\s+$")
_DOT_TIME = re.compile(r"(?<![\d.:/])(\d{1,2})\.(\d{2})(?![\d.:/])")
_TOKEN = re.compile(
    r"""
    (?P<time>(?<![\d:])\d{1,2}:\d{2}(?![\d:]))
  | (?P<date>(?<![\d.:/])\d{1,2}[./]\d{1,2}(?:[./](?:\d{4}|\d{2}))?(?![\d.:/]))
  | (?P<tag>\#[^\W_][\w-]*)
  | (?P<num>\d{1,9}(?:-?(?:е|го|й|ый|ое|ого|ому|ым|ая|ой|ую|ых))?(?![\d]))
  | (?P<word>[^\W\d_]+(?:-[^\W\d_]+)*)
  | (?P<bignum>\d+)
  | (?P<punct>\S)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # time | date | tag | num | word | punct
    text: str
    start: int
    end: int
    value: tuple[int, int, int | None] | int | None = None  # time/date/num payload
    ordinal: bool = False  # num written as "15-е"


def clean_and_normalize(raw: str) -> tuple[str, str, bool]:
    """Return ``(base, norm, truncated)``.

    ``base`` keeps the user's wording (NFC, single spaces, no control characters).
    ``norm`` is lower-cased, ``ё``-folded and has ``18.30`` rewritten as ``18:30``.
    """
    truncated = len(raw) > MAX_INPUT_LENGTH
    text = unicodedata.normalize("NFC", raw[:MAX_INPUT_LENGTH])
    kept: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cf" or (category == "Cc" and not ch.isspace()):
            continue
        kept.append(" " if ch.isspace() else ch)
    base = re.sub(r" {2,}", " ", "".join(kept)).strip()
    norm = "".join(_fold(ch) for ch in base)
    return base, _dots_to_colons(norm), truncated


def _fold(ch: str) -> str:
    lowered = ch.lower()
    if len(lowered) != 1:
        return ch
    return "е" if lowered == "ё" else lowered


def _dots_to_colons(norm: str) -> str:
    def repl(match: re.Match[str]) -> str:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 24 or minute > 59:  # noqa: PLR2004
            return match.group(0)
        after_preposition = bool(_TIME_PREPOSITIONS.search(norm[: match.start()]))
        if minute > 12 or after_preposition:  # noqa: PLR2004
            return f"{match.group(1)}:{match.group(2)}"
        return match.group(0)  # could be a day.month date

    return _DOT_TIME.sub(repl, norm)


def tokenize(norm: str) -> list[Token]:
    tokens: list[Token] = []
    for match in _TOKEN.finditer(norm):
        kind = match.lastgroup or "punct"
        text = match.group()
        start, end = match.span()
        if kind == "time":
            hour, minute = text.split(":")
            tokens.append(Token("time", text, start, end, (int(hour), int(minute), None)))
        elif kind == "date":
            parts = re.split(r"[./]", text)
            year = int(parts[2]) if len(parts) == 3 else None  # noqa: PLR2004
            if year is not None and year < 100:  # noqa: PLR2004
                year += 2000
            tokens.append(Token("date", text, start, end, (int(parts[0]), int(parts[1]), year)))
        elif kind == "num":
            digits = re.match(r"\d+", text)
            value = int(digits.group()) if digits else 0
            # "15-е" is an ordinal; a leading zero ("05", "00") is not
            tokens.append(Token("num", text, start, end, value, ordinal=not text.isdigit()))
        elif kind == "bignum":
            tokens.append(Token("word", text, start, end))  # too large to be a time/date
        else:
            tokens.append(Token(kind, text, start, end))
    return tokens
