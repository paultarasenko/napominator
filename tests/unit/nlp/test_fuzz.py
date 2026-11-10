"""Robustness: the parser never raises or hangs, and keeps its invariants."""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from napominator.domain.timeutil import local_to_utc
from napominator.nlp import ParseContext, RuleBasedParser
from napominator.nlp.models import MAX_INPUT_LENGTH
from napominator.nlp.tokenizer import clean_and_normalize

TZ = ZoneInfo("Europe/Moscow")
NOW = local_to_utc(datetime(2026, 10, 13, 15, 0), TZ)
CTX = ParseContext(now=NOW, tz=TZ, categories=("Работа", "Дом"))
PARSER = RuleBasedParser()

WORDS = [
    "завтра",
    "в",
    "9",
    "18:30",
    "через",
    "каждый",
    "день",
    "неделю",
    "часа",
    "минут",
    "по",
    "будням",
    "до",
    "конца",
    "месяца",
    "раз",
    "в",
    "утра",
    "вечера",
    "половине",
    "десятого",
    "без",
    "пятнадцати",
    "три",
    "#работа",
    "категория",
    "за",
    "важно",
    "пятницу",
    "28.10",
    "октября",
    "позвонить",
    "маме",
    "3",
    "кг",
    "яблок",
    "и",
    ",",
    "—",
    "😀",
    "15-е",
    "число",
    "последний",
    "ёж",
    "напомни",
    "пожалуйста",
    "полчаса",
    "пару",
    "полдень",
    "0",
    "24",
    "99",
]


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=300))
def test_arbitrary_unicode_never_raises(text: str) -> None:
    result = PARSER.parse(text, CTX)
    _check_invariants(text, result)


def test_letter_without_unicode_name_does_not_crash() -> None:
    # U+17000 has no name in some Python builds; pymorphy3 raises ValueError on it.
    text = "в 5 \U00017000"
    _check_invariants(text, PARSER.parse(text, CTX))


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(WORDS), max_size=25).map(" ".join))
def test_word_salad_keeps_invariants(text: str) -> None:
    _check_invariants(text, PARSER.parse(text, CTX))


def _check_invariants(text: str, result) -> None:  # type: ignore[no-untyped-def]
    base, norm, _ = clean_and_normalize(text)
    assert len(base) == len(norm)
    assert len(result.text) <= len(base) + 1
    assert 0.0 <= result.confidence <= 1.0
    if result.when is not None:
        assert result.when.tzinfo is None
        assert result.confidence > 0
    for span in result.spans:
        assert 0 <= span.start < span.end <= len(base)
    if result.recurrence is not None:
        assert result.when is not None


ADVERSARIAL = [
    "в " * 400,
    "через " * 300,
    "каждый " * 300,
    "1 " * 500,
    "в 9 " * 200,
    ":" * 800,
    "1." * 400,
    "а" * 5000,
    "😀" * 2000,
    "\u200b\u200d" * 300 + "завтра в 9",
    "0" * 2000,
    "каждый " + "вторник и " * 100 + "четверг",
    "завтра\n\n\t\r в\u00a09 утра\u2003позвонить",
    "\x00завтра\x07 в 9 утра\x1b",
]


@pytest.mark.parametrize("text", ADVERSARIAL, ids=[f"adv{i}" for i in range(len(ADVERSARIAL))])
def test_adversarial_inputs_finish_quickly(text: str) -> None:
    started = time.perf_counter()
    result = PARSER.parse(text, CTX)
    assert time.perf_counter() - started < 1.0
    _check_invariants(text, result)


def test_input_is_truncated_and_flagged() -> None:
    result = PARSER.parse("завтра в 9 утра позвонить " + "а" * 5000, CTX)
    assert "too_long" in {f.value for f in result.flags}
    assert len(result.text) <= MAX_INPUT_LENGTH


def test_control_characters_and_odd_whitespace_are_normalised() -> None:
    result = PARSER.parse("\x00завтра\x07 в\u00a09 утра\u2003позвонить", CTX)
    assert result.when == datetime(2026, 10, 14, 9, 0)
    assert result.text == "Позвонить"


def test_latency_budget_p95_under_20ms_for_200_chars() -> None:
    PARSER.parse("прогрев морфологии: завтра в 9 позвонить", CTX)  # load dictionaries once
    samples = [
        "напомни мне завтра в 18:30 позвонить маме и узнать про документы на оформление",
        "каждый вторник и четверг в 19:00 тренировка в зале на втором этаже обязательно",
        "через 2 часа 15 минут забрать посылку из пункта выдачи на улице Ленина дом 5",
        "в половине десятого вечера выключить духовку и закрыть все окна на кухне дома",
        "15 мая день рождения Ани каждый год подарить цветы и торт заранее заказать",
    ]
    samples = [(s + " " + s)[:200] for s in samples]
    timings: list[float] = []
    for _ in range(40):
        for text in samples:
            started = time.perf_counter()
            PARSER.parse(text, CTX)
            timings.append(time.perf_counter() - started)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    assert p95 < 0.020, (
        f"p95={p95 * 1000:.1f} ms, median={statistics.median(timings) * 1000:.1f} ms"
    )
    assert timedelta(0) == timedelta(0)
