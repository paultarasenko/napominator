"""Builds the reminder body: the user's own words minus the schedule phrases."""

from __future__ import annotations

import re

from napominator.nlp.lexicon import EDGE_WORDS

_LEADING_SERVICE = re.compile(
    r"""^(?:
        напомни(?:те|ть)?(?:\s+мне)?
      | напоминай(?:те)?(?:\s+мне)?
      | (?:поставь|создай|добавь|сделай)(?:те)?(?:\s+мне)?\s+(?:напоминани[ея]|напоминалку)
      | напоминани[ея](?=\s*[:\-–—]|\s+(?!(?:о|об|про|для|от)(?![\w-])))
      | (?:запиши|запишите)(?:\s+мне)?(?:\s+(?:напоминани[ея]|напоминалку))?
      | будь(?:те)?\s+добр(?:ы)?
      | хочу(?:,?\s+чтобы\s+(?:ты|вы))?\s+(?:напомнил[аи]?|напомнить|не\s+забыть)(?:\s+мне)?
      | чтобы\s+я\s+не\s+забыл[аи]?
      | не\s+дай(?:те)?(?:\s+мне)?\s+забыть
      | напомнишь(?:\s+мне)?
      | напомнил[аи]?(?:\s+мне)?
      | запомни(?:те)?
      | не\s+забудь(?:те)?
      | не\s+забыть
      | не\s+забывай(?:те)?
      | мне\s+(?:надо|нужно|необходимо)
      | надо | нужно | необходимо
      | пожалуйста | плиз | пжл
    )(?![\w-])(?:\s*,)?(?:\s+(?:о\s+том,?\s+)?что(?![\w-]))?\s*""",
    re.VERBOSE,
)
_TRAILING_SERVICE = re.compile(
    r"\s*(?<![\w-])(?:пожалуйста|плиз|пжл|напомни(?:те)?(?:\s+мне)?)[\s!.?…]*$"
)
_SYMBOLS_BEFORE_SERVICE = re.compile(r"^((?:[^\w\s]|_)+\s*)")
_LEADING_PUNCT = re.compile(r"^[\s,;:.\-–—!?…)(\]\[\"'«»]+")
_TRAILING_PUNCT = re.compile(r"[\s,;:.\-–—(\[]+$")
_FIRST_WORD = re.compile(r"^([^\W\d_]+)(?![\w-])\s*")
_LAST_WORD = re.compile(r"\s*(?<![\w-])([^\W\d_]+)$")


def build_text(base: str, norm: str, spans: list[tuple[int, int]]) -> str:
    """Cut ``spans`` (character ranges) out of ``base`` and tidy what remains."""
    merged = _merge(spans)
    kept_base: list[str] = []
    kept_norm: list[str] = []
    cursor = 0
    for start, end in merged:
        kept_base.append(base[cursor:start])
        kept_norm.append(norm[cursor:start])
        cursor = end
    kept_base.append(base[cursor:])
    kept_norm.append(norm[cursor:])
    text = " ".join(kept_base)
    low = " ".join(kept_norm)
    cut_left = bool(merged) and merged[0][0] == 0
    cut_right = bool(merged) and merged[-1][1] == len(base)
    return _tidy(text, low, cut_left=cut_left, cut_right=cut_right)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _tidy(text: str, low: str, *, cut_left: bool, cut_right: bool) -> str:
    text, low = _squeeze(text, low)
    while text:
        new_text, new_low = _one_pass(text, low, cut_left=cut_left, cut_right=cut_right)
        if new_text == text:
            break
        text, low = new_text, new_low
    text = text.strip()
    return text[:1].upper() + text[1:] if text else ""


def _one_pass(text: str, low: str, *, cut_left: bool, cut_right: bool) -> tuple[str, str]:
    text, low = _strip(_LEADING_PUNCT, text, low, left=True)
    text, low = _strip(_TRAILING_PUNCT, text, low, left=False)
    text, low = _strip(_LEADING_SERVICE, text, low, left=True)
    text, low = _strip_service_after_symbols(text, low)
    text, low = _strip(_TRAILING_SERVICE, text, low, left=False)
    if cut_left:
        # a leading "до" is kept: "завтра за час до работы выйти" -> "До работы выйти"
        text, low = _dangling(_FIRST_WORD, text, low, left=True, keep=frozenset({"до"}))
    if cut_right:
        text, low = _dangling(_LAST_WORD, text, low, left=False)
    return text, low


def _strip_service_after_symbols(text: str, low: str) -> tuple[str, str]:
    """``📞 напомни позвонить`` -> ``📞 позвонить``: emoji in front do not hide the service word."""
    lead = _SYMBOLS_BEFORE_SERVICE.match(low)
    if lead is None or lead.end() == 0:
        return text, low
    rest = _LEADING_SERVICE.match(low[lead.end() :])
    if rest is None or rest.end() == 0:
        return text, low
    cut = lead.end() + rest.end()
    return text[: lead.end()] + text[cut:], low[: lead.end()] + low[cut:]


def _squeeze(text: str, low: str) -> tuple[str, str]:
    out_t: list[str] = []
    out_l: list[str] = []
    previous_space = True
    for ch, lo in zip(text, low, strict=True):
        if ch == " ":
            if previous_space:
                continue
            previous_space = True
        else:
            previous_space = False
        out_t.append(ch)
        out_l.append(lo)
    return "".join(out_t).strip(), "".join(out_l).strip()


def _strip(pattern: re.Pattern[str], text: str, low: str, *, left: bool) -> tuple[str, str]:
    match = pattern.search(low)
    if match is None or match.end() == match.start():
        return text, low
    if left:
        return text[match.end() :], low[match.end() :]
    return text[: match.start()], low[: match.start()]


def _dangling(
    pattern: re.Pattern[str],
    text: str,
    low: str,
    *,
    left: bool,
    keep: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    match = pattern.search(low)
    if match is None or match.group(1) not in EDGE_WORDS or match.group(1) in keep:
        return text, low
    return _strip(pattern, text, low, left=left)
