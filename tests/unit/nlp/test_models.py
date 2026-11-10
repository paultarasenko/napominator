from __future__ import annotations

from datetime import datetime

import pytest

from napominator.nlp import CONFIDENCE_THRESHOLD, Flag, ParseResult

WHEN = datetime(2026, 10, 14, 9, 0)


def result(**kw: object) -> ParseResult:
    base: dict[str, object] = {"text": "Позвонить", "when": WHEN, "confidence": 1.0}
    base.update(kw)
    return ParseResult(**base)  # type: ignore[arg-type]


def test_threshold_boundary() -> None:
    assert not result(confidence=CONFIDENCE_THRESHOLD).needs_clarification
    assert result(confidence=CONFIDENCE_THRESHOLD - 0.001).needs_clarification


@pytest.mark.parametrize(
    "kw",
    [{"when": None}, {"text": ""}, {"flags": frozenset({Flag.PAST_DATE})}],
)
def test_blocking_conditions(kw: dict[str, object]) -> None:
    assert result(**kw).needs_clarification


def test_threshold_is_a_sensible_named_constant() -> None:
    assert 0.5 < CONFIDENCE_THRESHOLD < 0.9
