from __future__ import annotations

import pytest

from napominator.nlp.tokenizer import clean_and_normalize, tokenize


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("в 18.30 звонок", "в 18:30 звонок"),
        ("к 9.45", "к 9:45"),
        ("в 10.10 встреча", "в 10:10 встреча"),  # after "в" -> time
        ("10.10 встреча", "10.10 встреча"),  # bare -> could be a date, stays
        ("28.10 оплатить", "28.10 оплатить"),
        ("до 13.10 сдать", "до 13.10 сдать"),
        ("28.10.2027", "28.10.2027"),
        ("1.5 кг", "1.5 кг"),
        ("ЁЛКА Ёж", "елка еж"),
    ],
)
def test_normalisation(raw: str, norm: str) -> None:
    assert clean_and_normalize(raw)[1] == norm


def test_base_keeps_user_wording_and_alignment() -> None:
    base, norm, truncated = clean_and_normalize("  Ёлку  купить\u200b  ")
    assert base == "Ёлку купить"
    assert len(base) == len(norm)
    assert not truncated


def test_token_kinds() -> None:
    kinds = [(t.kind, t.text) for t in tokenize("в 18:30 28.10 15-е #работа,x 5")]
    assert kinds == [
        ("word", "в"),
        ("time", "18:30"),
        ("date", "28.10"),
        ("num", "15-е"),
        ("tag", "#работа"),
        ("punct", ","),
        ("word", "x"),
        ("num", "5"),
    ]


def test_huge_numbers_are_not_times_or_dates() -> None:
    assert tokenize("12345678901234567890")[0].kind == "word"
