from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from napominator.domain.timeutil import local_to_utc
from napominator.nlp import Flag, ParseContext, RuleBasedParser
from tests.unit.nlp.corpus import CASES, Case

TZ = ZoneInfo("Europe/Moscow")
NOW = local_to_utc(datetime(2026, 10, 13, 15, 0), TZ)
PARSER = RuleBasedParser()


def test_corpus_is_large_enough() -> None:
    assert len(CASES) >= 150


@pytest.mark.parametrize("case", CASES, ids=[c.text for c in CASES])
def test_corpus(case: Case) -> None:
    result = PARSER.parse(case.text, ParseContext(now=NOW, tz=TZ))
    expected_when = datetime.fromisoformat(case.when) if case.when else None
    assert result.when == expected_when, result
    assert result.text == case.body, result
    rrule = result.recurrence.to_rrule() if result.recurrence else None
    assert rrule == case.rrule, result
    for flag in case.flags.split():
        assert Flag(flag) in result.flags, (flag, result.flags)
    assert result.needs_clarification is case.clarify, result
    assert result.category_ref == case.ref
    assert result.lead == case.lead
    assert result.nag is case.nag
