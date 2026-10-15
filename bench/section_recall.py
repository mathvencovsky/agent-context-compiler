"""Section recall for M10a: how much of the answer-key section a context holds.

A section (``bench.anchor_qrels``) is cut into units: its lines, split again at
sentence ends, kept when they have at least ``MIN_WORDS`` words. Text is
compared after :func:`normalize`: lowercase, whitespace collapsed, and the marks
extractors render differently deleted (Markdown emphasis, code and heading
marks, backslash escapes, table pipes, quote markers, ¶). :func:`coverage` is
the share of units found in a context; a task is a hit when coverage is at least
``HIT``. These rules are fixed before any system is measured.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

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
