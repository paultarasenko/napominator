"""Category use cases and the "which category is this?" decision."""

from __future__ import annotations

from dataclasses import dataclass

from napominator.application.errors import (
    DuplicateCategoryError,
    LimitExceededError,
    NotFoundError,
)
from napominator.application.ports import UnitOfWork
from napominator.domain.categories import (
    DEFAULT_CATEGORIES,
    CategoryInput,
    guess_emoji,
    parse_category_input,
    split_emoji,
)
from napominator.domain.entities import MAX_CATEGORIES, Category
from napominator.nlp.categories import match_existing, significant_lemmas, suggest
from napominator.nlp.models import ParseResult

_MAX_HINT_TOKENS = 8


async def create_default_categories(uow: UnitOfWork, user_id: int) -> list[Category]:
    created: list[Category] = []
    for emoji, name in DEFAULT_CATEGORIES:
        category = await uow.categories.add(user_id, name, emoji)
        if category is not None:
            created.append(category)
    return created


async def create_category(uow: UnitOfWork, user_id: int, text: str) -> Category:
    """Raises ``InvalidCategoryNameError``, ``LimitExceededError`` or ``DuplicateCategoryError``."""
    parsed = parse_category_input(text)
    if await uow.categories.count(user_id) >= MAX_CATEGORIES:
        raise LimitExceededError("categories", MAX_CATEGORIES)
    category = await uow.categories.add(user_id, parsed.name, parsed.emoji)
    if category is None:
        raise DuplicateCategoryError(parsed.name)
    return category


async def rename_category(uow: UnitOfWork, user_id: int, category_id: int, text: str) -> Category:
    parsed: CategoryInput = parse_category_input(text)
    changes: dict[str, object] = {"name": parsed.name}
    if split_emoji(text)[0]:
        changes["emoji"] = parsed.emoji
    if not await uow.categories.update(category_id, user_id, **changes):
        existing = await uow.categories.get(category_id, user_id)
        if existing is None:
            raise NotFoundError("category")
        raise DuplicateCategoryError(parsed.name)
    category = await uow.categories.get(category_id, user_id)
    assert category is not None  # noqa: S101 - just updated
    return category


async def set_category_emoji(uow: UnitOfWork, user_id: int, category_id: int, text: str) -> None:
    emoji, _ = split_emoji(text)
    if not emoji or not await uow.categories.update(category_id, user_id, emoji=emoji):
        raise NotFoundError("category")


async def delete_category(
    uow: UnitOfWork, user_id: int, category_id: int, *, move_to: int | None
) -> int:
    """Delete a category, moving its reminders to ``move_to`` (or leaving them uncategorised).

    Returns how many reminders were moved. The reminders are never deleted.
    """
    if await uow.categories.get(category_id, user_id) is None:
        raise NotFoundError("category")
    if move_to is not None and (
        move_to == category_id or await uow.categories.get(move_to, user_id) is None
    ):
        raise NotFoundError("target category")
    moved = await uow.categories.move_reminders(user_id, category_id, move_to)
    await uow.categories.delete(category_id, user_id)
    return moved


async def learn(uow: UnitOfWork, user_id: int, text: str, category_id: int) -> None:
    """Remember that texts like this one belong to ``category_id`` (explicit user choices only)."""
    tokens = list(dict.fromkeys(significant_lemmas(text)))[:_MAX_HINT_TOKENS]
    await uow.hints.bump(user_id, tokens, category_id)


@dataclass(frozen=True, slots=True)
class CategoryChoice:
    category: Category | None
    explicit: bool  # the user wrote it (#tag / "категория X"), as opposed to a suggestion


async def choose_category(uow: UnitOfWork, user_id: int, parsed: ParseResult) -> CategoryChoice:
    """Explicit mention wins (created on the fly), then learned habits, then the dictionary."""
    categories = await uow.categories.list(user_id)
    by_name = {c.name: c for c in categories}
    if parsed.category_ref:
        match = parsed.category or match_existing(parsed.category_ref, by_name)
        if match is not None and match in by_name:
            return CategoryChoice(by_name[match], explicit=True)
        try:
            return CategoryChoice(await create_category(uow, user_id, parsed.category_ref), True)
        except (LimitExceededError, DuplicateCategoryError, ValueError):
            return CategoryChoice(None, explicit=True)
    if not parsed.text or not categories:
        return CategoryChoice(None, explicit=False)
    lemmas = significant_lemmas(parsed.text)
    learned_raw = await uow.hints.learned(user_id, lemmas)
    ids = {c.id: c.name for c in categories}
    learned = {
        token: {ids[cid]: count} for token, (cid, count) in learned_raw.items() if cid in ids
    }
    name = suggest(parsed.text, by_name, learned)
    return CategoryChoice(by_name.get(name) if name else None, explicit=False)


def emoji_for(category: Category | None) -> str:
    if category is None:
        return "▫️"
    return category.emoji or guess_emoji(category.name)
