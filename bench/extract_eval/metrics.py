"""Deterministic extraction-quality metrics (pure functions, no I/O).

HTML metrics compare an extractor's text with the WCXB ground truth; PDF
metrics check hand-written facts (``pdf_facts.yaml``) against a NINA
``Document``. Everything here is offline and seeded.
"""

from __future__ import annotations

import random
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from app.extract.ir import Block, Document

_WORD = re.compile(r"\w+")
_ALPHA = re.compile(r"[^\W\d_]+")
_CAMEL_JOIN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_LINENO = re.compile(r"^\s*(\d{1,4})(?=\S)")

#: Class suffixes of highlight.js / Pygments token spans, which are not
#: languages; seeing one as a code block's language is the ``hljs-`` bug.
TOKEN_CLASS_NAMES = frozenset(
    {
        "keyword", "string", "comment", "number", "built_in", "title",
        "function", "params", "attr", "attribute", "literal", "meta", "name",
        "tag", "variable", "type", "symbol", "section", "selector-tag",
        "punctuation", "operator", "property", "class", "subst", "doctag",
    }
)  # fmt: skip


# ---------------------------------------------------------------- text basics


def tokenize(text: str | None) -> list[str]:
    """Lowercase word tokens, as in WCXB's ``evaluate.py``."""
    return _WORD.findall((text or "").lower())


def collapse(text: str | None) -> str:
    """Collapse every whitespace run to one space."""
    return " ".join((text or "").split())


def word_prf(predicted: str, reference: str) -> tuple[float, float, float]:
    """Word-level precision, recall and F1 (WCXB's official metric)."""
    pred, ref = Counter(tokenize(predicted)), Counter(tokenize(reference))
    if not ref:
        return (1.0, 1.0, 1.0) if not pred else (0.0, 0.0, 0.0)
    if not pred:
        return (0.0, 0.0, 0.0)
    overlap = sum((pred & ref).values())
    p, r = overlap / sum(pred.values()), overlap / sum(ref.values())
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def leak_rate(predicted: str, reference: str) -> float:
    """Share of extracted tokens whose word never occurs in the ground truth.

    This is boilerplate in the strict sense (navigation, widgets, labels).
    Repeating a ground-truth word too often (duplicated code) lowers precision
    but is not a leak.
    """
    pred = tokenize(predicted)
    if not pred:
        return 0.0
    vocab = set(tokenize(reference))
    return sum(t not in vocab for t in pred) / len(pred)


def snippet_rate(text: str, snippets: Sequence[str] | None) -> float | None:
    """Share of snippets found (case-insensitive); None when there are none."""
    if not snippets:
        return None
    low = text.lower()
    return sum(s.lower() in low for s in snippets) / len(snippets)


def glued_tokens(text: str, vocab: set[str]) -> list[str]:
    """Tokens made of two ground-truth words joined at a case boundary.

    ``AttributeData`` counts when ``attributedata`` is not itself a word of the
    ground truth but ``attribute`` and ``data`` are (both longer than 2). This
    is the block-boundary glue of research front C (``glue.py``).
    """
    found = []
    for token in _WORD.findall(text):
        if token.lower() in vocab:
            continue
        for match in _CAMEL_JOIN.finditer(token):
            left, right = token[: match.start()].lower(), token[match.start() :].lower()
            if len(left) > 2 and len(right) > 2 and left in vocab and right in vocab:
                found.append(token)
                break
    return found


def long_token_stats(text: str, min_len: int = 20) -> tuple[int, int]:
    """(alphabetic tokens of ``min_len``+ letters, alphabetic tokens).

    In English and in most technical German, a 20-letter run is almost always
    words glued together by an extractor that lost the spaces.
    """
    words = _ALPHA.findall(text)
    return sum(len(w) >= min_len for w in words), len(words)


# ---------------------------------------------------------------- code


def markdown_code_blocks(markdown: str) -> list[str]:
    """Bodies of the fenced code blocks of a Markdown string."""
    blocks: list[str] = []
    current: list[str] | None = None
    fence = ""
    for line in markdown.splitlines():
        match = _FENCE.match(line)
        if current is None:
            if match:
                current, fence = [], match.group(1)[0]
        elif match and match.group(1)[0] == fence and not line.strip()[3:].strip():
            blocks.append("\n".join(current))
            current = None
        else:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


def contained(
    needles: Iterable[str], haystack: str, min_chars: int = 12
) -> tuple[int, int]:
    """(needles found, needles counted), whitespace-collapsed and lowercased.

    Needles shorter than ``min_chars`` after collapsing are skipped: a one-word
    snippet proves nothing about fidelity.
    """
    hay = collapse(haystack).lower()
    found = total = 0
    for needle in needles:
        n = collapse(needle).lower()
        if len(n) < min_chars:
            continue
        total += 1
        found += n in hay
    return found, total


def has_line_number_leak(code: str, min_run: int = 3) -> bool:
    """True when ``min_run``+ consecutive lines start with 1, 2, 3... glued on.

    That is the Sphinx ``linenos`` failure: ``1import os`` / ``2print(1)``.
    """
    run = 0
    previous: int | None = None
    for line in code.splitlines():
        match = _LINENO.match(line)
        if match is None:
            run, previous = 0, None
            continue
        number = int(match.group(1))
        run = run + 1 if previous is not None and number == previous + 1 else 1
        previous = number
        if run >= min_run:
            return True
    return False


# ---------------------------------------------------------------- tables


def markdown_table_cells(markdown: str) -> list[list[str]]:
    """Cells of every GFM table of a Markdown string, one list per table."""
    lines = markdown.splitlines()
    tables: list[list[str]] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and "|" in lines[i] and _TABLE_SEP.match(lines[i + 1]):
            cells = _row_cells(lines[i])
            i += 2
            while i < len(lines) and "|" in lines[i]:
                cells.extend(_row_cells(lines[i]))
                i += 1
            tables.append([c for c in cells if c])
        else:
            i += 1
    return tables


def _row_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [collapse(c) for c in body.split("|")]


def block_cells(block: Block) -> list[str]:
    """Every header and body cell of a NINA table block."""
    data = block.data
    cells = [str(c) for c in data.get("headers") or []]  # type: ignore[attr-defined]
    for row in data.get("rows") or []:  # type: ignore[attr-defined]
        cells.extend(str(c) for c in row)
    return cells


def is_empty_table(block: Block) -> bool:
    return block.type == "table" and not any(c.strip() for c in block_cells(block))


# ---------------------------------------------------------------- NINA docs


def block_text(block: Block) -> str:
    """A block's text, with code taken verbatim from ``data["code"]``."""
    if block.type == "code":
        return str(block.data.get("code") or "")
    return block.text


def document_text(doc: Document) -> str:
    return "\n".join(block_text(b) for b in doc.blocks)


# ---------------------------------------------------------------- PDF facts


def evaluate_facts(doc: Document, facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Check one document's facts; one row per fact, with a stable id.

    Fact ids are ``<kind>:<index>`` in file order, so appending a fact never
    renames an existing one.
    """
    text = collapse(document_text(doc))
    headings = [collapse(b.text) for b in doc.blocks if b.type == "heading"]
    tables = [b for b in doc.blocks if b.type == "table"]
    cells = {collapse(c) for t in tables for c in block_cells(t)}
    rows: list[dict[str, Any]] = []

    def add(kind: str, index: int, passed: bool, detail: str) -> None:
        rows.append(_fact(kind, index, passed, detail))

    if facts.get("title"):
        title = collapse(facts["title"])
        early = " ".join(
            collapse(b.text)
            for b in doc.blocks
            if b.type == "heading" and (b.page or 1) <= 2
        )
        meta = collapse(doc.metadata.title)
        add("title", 0, title in meta or title in early, title)
    for i, h in enumerate(facts.get("headings") or []):
        add("heading", i, any(collapse(h) in x for x in headings), h)
    rows.extend(evaluate_text_facts(text, facts))
    spec = facts.get("tables")
    if spec is not None:
        n = len(tables)
        add(
            "table_count",
            0,
            spec["min"] <= n <= spec["max"],
            f"{spec['min']}..{spec['max']}",
        )
        for i, cell in enumerate(spec.get("cells") or []):
            add("table_cell", i, collapse(cell) in cells, cell)
    for i, rng in enumerate(facts.get("paragraphs") or []):
        n = sum(
            1
            for b in doc.blocks
            if b.page == rng["page"] and b.type not in ("heading", "table")
        )
        add(
            "paragraphs",
            i,
            rng["min"] <= n <= rng["max"],
            f"page {rng['page']}: {rng['min']}..{rng['max']} (got {n})",
        )
    return rows


def evaluate_text_facts(text: str, facts: dict[str, Any]) -> list[dict[str, Any]]:
    """The facts a plain-text extractor can be held to: ``present`` and ``order``."""
    text = collapse(text)
    rows = []
    for i, s in enumerate(facts.get("present") or []):
        rows.append(_fact("present", i, collapse(s) in text, s))
    for i, (first, second) in enumerate(facts.get("order") or []):
        a, b = text.find(collapse(first)), text.find(collapse(second))
        rows.append(
            _fact("order", i, a != -1 and b != -1 and a < b, f"{first} < {second}")
        )
    return rows


def _fact(kind: str, index: int, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": f"{kind}:{index}", "kind": kind, "passed": passed, "fact": detail}


def paragraphs_per_page(doc: Document) -> float:
    """Mean text blocks (not heading/table) per page that has any block."""
    per_page = Counter(b.page for b in doc.blocks if b.type not in ("heading", "table"))
    pages = {b.page for b in doc.blocks}
    return sum(per_page.values()) / max(len(pages), 1)


# ---------------------------------------------------------------- statistics


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def bootstrap_ci(
    values: Sequence[float],
    *,
    seed: int = 0,
    resamples: int = 2000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean, seeded (deterministic)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        statistics.fmean(values[rng.randrange(n)] for _ in range(n))
        for _ in range(resamples)
    )
    lo = means[int(alpha / 2 * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return lo, hi


def paired_bootstrap_ci(
    before: Sequence[float], after: Sequence[float], **kwargs: Any
) -> tuple[float, float, float]:
    """(mean of after - before, CI low, CI high) over paired pages."""
    if len(before) != len(after):
        raise ValueError("paired samples must have the same length")
    diffs = [b - a for a, b in zip(before, after, strict=True)]
    lo, hi = bootstrap_ci(diffs, **kwargs)
    return mean(diffs), lo, hi
