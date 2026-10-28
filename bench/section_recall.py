"""Section recall for M10a: how much of the answer-key section a context holds.

A section (``bench.anchor_qrels``) is cut into units: its lines, split again at
sentence ends, kept when they have at least ``MIN_WORDS`` words. Text is
compared after :func:`normalize`: lowercase, whitespace collapsed, and the marks
extractors render differently deleted (Markdown emphasis, code and heading
marks, backslash escapes, table pipes, quote markers, ¶). :func:`coverage` is
the share of units found in a context; a task is a hit when coverage is at least
``HIT``. These rules are fixed before any system is measured.

:func:`token_precision` adds the other side (2026-10-28, before any system was
measured with it): the share of a context's tokens that belong to the section,
and the token-level intersection over union (Chroma, "Evaluating Chunking
Strategies for Retrieval", 2024). Coverage says whether the answer arrived;
precision says how much of what the agent paid for was the answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.index.tokens import count_tokens

#: Shortest unit kept; shorter lines are headings or labels, not answers.
MIN_WORDS = 4
#: Coverage at or above which the section counts as delivered.
HIT = 0.5

_MARKS = re.compile(r"[*`#>|\\¶]")
_SPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def normalize(text: str) -> str:
    """Lowercase, marks deleted, whitespace collapsed."""
    return _SPACE.sub(" ", _MARKS.sub("", text.lower())).strip()


def units(section: str) -> list[str]:
    """The section's normalized sentences, deduplicated, in order."""
    found: dict[str, None] = {}
    for line in section.splitlines():
        for piece in _SENTENCE_END.split(line):
            unit = normalize(piece)
            if len(unit.split()) >= MIN_WORDS:
                found[unit] = None
    return list(found)


def coverage(section_units: Sequence[str], context: str) -> float:
    """Share of ``section_units`` present in ``context`` (0.0 if none)."""
    if not section_units:
        return 0.0
    haystack = normalize(context)
    return sum(unit in haystack for unit in section_units) / len(section_units)


def first_hit(section_units: Sequence[str], context: str) -> int | None:
    """1-based position of the first ``[URL]`` fragment that holds a unit."""
    fragments = [f for f in context.split("[URL] ") if f.strip()]
    for position, fragment in enumerate(fragments, 1):
        haystack = normalize(fragment)
        if any(unit in haystack for unit in section_units):
            return position
    return None


@dataclass(frozen=True)
class TokenPrecision:
    """How much of a context is the section, in ``cl100k_base`` tokens."""

    on_section: int  # tokens of context pieces found in the section
    context: int  # all tokens of the context
    precision: float  # on_section / context
    iou: float  # on_section / (context + section - on_section)


def token_precision(section: str, context: str) -> TokenPrecision:
    """Token-level precision and IoU of ``context`` against ``section``.

    The context is cut like :func:`units` (lines, then sentence ends). A piece
    is on the section when it has at least ``MIN_WORDS`` words and its
    normalized text is in the normalized section. Each distinct piece counts
    once: a repeated sentence is waste, not answer. Everything else (headers,
    URLs, short labels, other text) counts only in the denominator.
    """
    haystack = normalize(section)
    seen: set[str] = set()
    on = 0
    for line in context.splitlines():
        for piece in _SENTENCE_END.split(line):
            unit = normalize(piece)
            if len(unit.split()) < MIN_WORDS or unit in seen:
                continue
            if unit in haystack:
                seen.add(unit)
                on += count_tokens(piece)
    total = count_tokens(context)
    union = total + count_tokens(section) - on
    return TokenPrecision(
        on_section=on,
        context=total,
        precision=on / total if total else 0.0,
        iou=on / union if union > 0 else 0.0,
    )
