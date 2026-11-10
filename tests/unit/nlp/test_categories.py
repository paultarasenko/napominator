from __future__ import annotations

from napominator.nlp.categories import match_existing, significant_lemmas, suggest

NAMES = ["Дом", "Работа", "Здоровье", "Финансы", "Покупки", "Даты"]


def test_exact_and_fuzzy_match() -> None:
    assert match_existing("работа", NAMES) == "Работа"
    assert match_existing("рабта", NAMES) == "Работа"
    assert match_existing("здоровья", NAMES) == "Здоровье"
    assert match_existing("хобби", NAMES) is None
    assert match_existing("", NAMES) is None
    assert match_existing("работа", []) is None


def test_dictionary_hint_only_for_existing_categories() -> None:
    assert suggest("Врач", NAMES) == "Здоровье"
    assert suggest("Оплатить аренду", NAMES) == "Финансы"
    assert suggest("Врач", ["Дом"]) is None
    assert suggest("Привет", NAMES) is None
    assert suggest("Врач", []) is None


def test_learned_history_beats_the_dictionary() -> None:
    learned = {"врач": {"Работа": 3, "Здоровье": 1}}
    assert suggest("Врач", NAMES, learned) == "Работа"
    assert suggest("Врач", NAMES, {"врач": {"Удалённая": 5}}) == "Здоровье"


def test_significant_lemmas() -> None:
    assert "аренда" in significant_lemmas("Оплатить аренду")
    assert significant_lemmas("в на 12") == []
