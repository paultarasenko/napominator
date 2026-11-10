"""Rule-based Russian reminder parser: pure, deterministic, offline."""

from __future__ import annotations

from napominator.nlp.categories import match_existing
from napominator.nlp.cleaner import build_text
from napominator.nlp.fragments import Fragment
from napominator.nlp.lexicon import genitive_to_nominative
from napominator.nlp.matchers import Scanner
from napominator.nlp.models import Flag, ParseContext, ParseResult, Span
from napominator.nlp.resolver import confidence_for, resolve, select
from napominator.nlp.tokenizer import clean_and_normalize, tokenize


class RuleBasedParser:
    """Implements ``ReminderParser``: ``parse(text, ctx) -> ParseResult``."""

    def parse(self, text: str, ctx: ParseContext) -> ParseResult:
        base, norm, truncated = clean_and_normalize(text)
        tokens = tokenize(norm)
        chosen = select(Scanner(tokens).scan())
        resolution = resolve(chosen, tokens, ctx)

        flags = set(resolution.flags)
        if truncated:
            flags.add(Flag.TOO_LONG)
        spans = _char_spans(resolution.used, tokens)
        body = build_text(base, norm, [(s.start, s.end) for s in spans])
        if resolution.noun_lead:
            body = _name_the_event(body)
        if not body:
            flags.add(Flag.EMPTY_TEXT)

        category = (
            match_existing(resolution.category_ref, ctx.categories)
            if resolution.category_ref
            else None
        )
        return ParseResult(
            text=body,
            when=resolution.when,
            recurrence=resolution.recurrence,
            category_ref=resolution.category_ref,
            category=category,
            lead=resolution.lead,
            nag=resolution.nag,
            confidence=confidence_for(resolution.when, flags),
            flags=frozenset(flags),
            spans=tuple(spans),
        )


def _char_spans(used: list[Fragment], tokens: list) -> list[Span]:  # type: ignore[type-arg]
    spans = [
        Span(tokens[f.start].start, tokens[f.end - 1].end, type(f.data).__name__)
        for f in used
        if 0 <= f.start < f.end <= len(tokens)
    ]
    return sorted(spans, key=lambda s: s.start)


def _name_the_event(body: str) -> str:
    """``До встречи`` -> ``Встреча`` when the whole text is just the event after "до"."""
    words = body.split()
    if len(words) not in {2, 3} or words[0].lower() != "до":
        return body
    head = genitive_to_nominative(words[1].lower())
    if head is None:
        return body
    if len(words) == 3 and genitive_to_nominative(words[2].lower()) is None:  # noqa: PLR2004
        return body  # "до встречи собраться": a task follows, keep the words as typed
    rest = " ".join(words[2:])
    return (head[:1].upper() + head[1:] + (" " + rest if rest else "")).strip()
