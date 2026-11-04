"""Low-budget mode: keep only the query-relevant sentences of selected chunks.

With budgets of 500 to 1,000 tokens one or two whole chunks fill the budget, and
most of each chunk is not the answer: on the M10a dev slice the hit rate falls
from 0.59 at 4k tokens to 0.29 at 1k (docs/pesquisa/rodada4/K_eficiencia.md,
L7). ``compact`` (SPEC §8, off by default) selects chunks as usual with a pool
budget of :data:`POOL_FACTOR` × ``max_tokens`` (capped at 32,000), then fills
``max_tokens`` with the best UNITS of all pooled chunks:

1. **Units.** Each chunk's Markdown is cut into units: a fenced code block, a
   table (consecutive ``|`` lines) and a heading are one unit each and are
   never cut; any other line is cut at sentence ends (``.``, ``!`` or ``?``
   followed by whitespace). A unit is a ``[start, end)`` span of the chunk, so
   kept text is always a literal substring of the page's Markdown.
2. **Score.** BM25 (k1 = 1.2, b = 0.75; Lucene's ``PassageScorer``) of the
   query's terms (lowercase, accent-free alphanumeric runs, minus
   :data:`STOPWORDS`) over the units of the pool, IDF counted over the same units, plus
   :data:`IDENTIFIER_BONUS` for each identifier of the query (``foo_bar``,
   ``--flag``, ``Class.method``, ``camelCase``, a backtick span) found
   literally (case-sensitive) in the unit. A unit then gains
   :data:`WINDOW_WEIGHT` × the scores of its neighbours on the same line or
   paragraph (sentence window), so the sentence around a hit can come along.
   Headings score 0. Units still at 0 are never kept: the number of relevant
   sentences ranges from none to all (Provence, ICLR 2025), so there is no
   fixed top-k.
3. **Fill.** Units are taken by score / sqrt(tokens), like chunk selection
   (``app/retrieve/select.py``), while they fit; a unit's cost includes its
   heading when the heading is not kept yet. Kept units are put back in page
   order; each gap of dropped units becomes one ``[…]`` line (:data:`ELISION`).
   Chunks with nothing kept are dropped. The total is re-counted with
   ``cl100k_base`` and the lowest-ranked units leave until it fits.
4. **Fallback.** When no unit of the pool overlaps the query, whole chunks are
   kept by value / sqrt(tokens) under ``max_tokens`` (``λ = 0`` selection) and
   nothing is pruned; the outcome says ``matched=False`` so the caller can tell
   this apart from "nothing needed pruning".

The constants are fixed before any measurement (DECISIONS.md 2026-10-29); the
acceptance rule lives in ``bench/compact_eval.py``. Pure and deterministic: no
I/O, no LLM, no model.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from app.index.tokens import count_tokens
from app.retrieve.assemble import AssembleResult
from app.retrieve.lexical import tokenize

__all__ = [
    "ELISION",
    "POOL_FACTOR",
    "CompactOutcome",
    "compact_pieces",
    "compact_result",
    "pool_budget",
]

#: Pool budget = POOL_FACTOR × max_tokens (research L7: "cerca de 4×B").
POOL_FACTOR = 4
#: Upper bound of ``max_tokens`` (SPEC §8); the pool never asks for more.
MAX_POOL_TOKENS = 32_000
#: The line that stands for dropped units.
ELISION = "[…]"
K1 = 1.2
B = 0.75
IDENTIFIER_BONUS = 2.0
WINDOW_WEIGHT = 0.25

#: Function words that carry no topic; IDF alone is unreliable over few units.
STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from how i if in into is it "
    "its me my of on or should that the this to use used using was what when "
    "where which who why will with would you your "
    # Portuguese, accents removed like :func:`tokenize` does.
    "como para uma um uns umas de do da dos das em no na nos nas o os as e ou "
    "que por com se qual quais quando onde ao aos pelo pela pelos pelas sao "
    "sou ser foi era esta este isso isto ha mais mas sem sobre entre ate "
    "meu minha seu sua posso pode devo preciso fazer".split()
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_IDENTIFIER = re.compile(
    r"`([^`\n]{2,80})`"  # a backtick span
    r"|(?<![\w-])(--?[A-Za-z][\w-]+)"  # --flag, -x
    r"|\b([A-Za-z_]\w*(?:(?:\.|::)[A-Za-z_]\w*)+)(?:\(\))?"  # a.b, A::b, a.b()
    r"|\b(\w*_\w+)\b"  # snake_case, _private
    r"|\b([a-z]+[A-Z]\w*)\b"  # camelCase
    r"|\b([A-Z][a-z0-9]+[A-Z]\w*)\b"  # PascalCase
    r"|\b(\w+)\(\)"  # func()
)


def pool_budget(max_tokens: int) -> int:
    """The selection budget used to gather the units for ``max_tokens``."""
    return min(POOL_FACTOR * max_tokens, MAX_POOL_TOKENS)


@dataclass(frozen=True)
class _Unit:
    piece: int
    index: int  # position within the piece
    start: int
    end: int
    kind: str  # "text", "code", "table" or "heading"
    group: int  # units of one line/paragraph share a group (sentence window)
    heading: int | None  # index of the governing heading unit in the piece


@dataclass(frozen=True)
class CompactOutcome:
    """The compacted text of each piece (``None``: dropped) and what happened.

    ``pruned`` is true when any unit was left out. ``dropped_relevant`` is true
    when a unit that overlaps the query did not fit the budget. ``matched`` is
    false when no unit overlapped the query (the fallback kept whole chunks).
    """

    texts: list[str | None]
    pruned: bool
    dropped_relevant: bool
    matched: bool = True


def _units(md: str, piece: int) -> list[_Unit]:
    """Cut ``md`` into units (module docstring, step 1)."""
    units: list[_Unit] = []
    lines = md.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    current: list[int | None] = [None]  # the latest heading unit's index

    def add(start: int, end: int, kind: str, group: int) -> None:
        # Trim surrounding whitespace so a unit is just its text.
        while start < end and md[start].isspace():
            start += 1
        while end > start and md[end - 1].isspace():
            end -= 1
        if start < end:
            index = len(units)
            units.append(_Unit(piece, index, start, end, kind, group, current[0]))
            if kind == "heading":
                current[0] = index

    i = 0
    group = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith(marker):
                j += 1
            end_line = min(j + 1, len(lines))
            group += 1
            add(offsets[i], offsets[end_line], "code", group)
            i = end_line
            continue
        if line.lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            group += 1
            add(offsets[i], offsets[j], "table", group)
            i = j
            continue
        if _HEADING.match(line):
            group += 1
            add(offsets[i], offsets[i + 1], "heading", group)
            i += 1
            continue
        if line.strip():
            group += 1
            start = offsets[i]
            for gap in _SENTENCE_END.finditer(line.rstrip("\r\n")):
                add(start, offsets[i] + gap.start(), "text", group)
                start = offsets[i] + gap.end()
            add(start, offsets[i + 1], "text", group)
        i += 1
    return units


def _identifiers(query: str) -> list[str]:
    found: dict[str, None] = {}
    for match in _IDENTIFIER.finditer(query):
        value = next(g for g in match.groups() if g)
        found[value] = None
    return list(found)


def _terms(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS]


def _scores(query: str, texts: Sequence[str], units: Sequence[_Unit]) -> list[float]:
    """Own score of each unit: BM25 + identifier bonus (step 2, before window)."""
    query_terms = set(_terms(query))
    identifiers = _identifiers(query)
    unit_terms = [_terms(texts[u.piece][u.start : u.end]) for u in units]
    n = len(units)
    avgdl = sum(len(t) for t in unit_terms) / n if n else 0.0
    df = {t: sum(1 for terms in unit_terms if t in terms) for t in query_terms}
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in query_terms}
    scores: list[float] = []
    for unit, terms in zip(units, unit_terms, strict=True):
        if unit.kind == "heading":
            scores.append(0.0)
            continue
        score = 0.0
        dl = len(terms)
        for term in query_terms:
            tf = terms.count(term)
            if tf:
                norm = K1 * (1 - B + B * dl / avgdl) if avgdl else K1
                score += idf[term] * tf * (K1 + 1) / (tf + norm)
        text = texts[unit.piece][unit.start : unit.end]
        score += IDENTIFIER_BONUS * sum(1 for ident in identifiers if ident in text)
        scores.append(score)
    return scores


def _scored_by(
    scorer: Callable[[Sequence[str]], Sequence[float]],
    texts: Sequence[str],
    units: Sequence[_Unit],
) -> list[float]:
    """``scorer`` over the non-heading units, with headings at 0 as in BM25."""
    body = [u for u in units if u.kind != "heading"]
    scores = scorer([texts[u.piece][u.start : u.end] for u in body]) if body else []
    by_unit = dict(zip(((u.piece, u.index) for u in body), scores, strict=True))
    return [by_unit.get((u.piece, u.index), 0.0) for u in units]


def _render(md: str, units: Sequence[_Unit], kept: set[int]) -> str | None:
    """The kept units of one piece in page order, gaps as :data:`ELISION`."""
    if not kept:
        return None
    parts: list[str] = []
    run_start: _Unit | None = None
    previous: _Unit | None = None
    for unit in units:
        if unit.index in kept:
            if run_start is None:
                if parts or unit.index > 0:
                    parts.append(ELISION)
                run_start = unit
            previous = unit
        elif run_start is not None and previous is not None:
            parts.append(md[run_start.start : previous.end])
            run_start = None
    if run_start is not None and previous is not None:
        parts.append(md[run_start.start : previous.end])
        if previous.index < units[-1].index:
            parts.append(ELISION)
    elif parts and parts[-1] != ELISION:
        parts.append(ELISION)
    return "\n".join(parts)


def _fallback(
    texts: Sequence[str], values: Sequence[float], budget: int
) -> CompactOutcome:
    """Whole pieces by value / sqrt(tokens) under ``budget`` (step 4)."""
    tokens = [count_tokens(t) for t in texts]
    order = sorted(
        range(len(texts)),
        key=lambda i: (-values[i] / math.sqrt(tokens[i]) if tokens[i] else math.inf, i),
    )
    left = budget
    chosen: set[int] = set()
    for i in order:
        if 0 < tokens[i] <= left:
            chosen.add(i)
            left -= tokens[i]
    return CompactOutcome(
        texts=[t if i in chosen else None for i, t in enumerate(texts)],
        pruned=False,
        dropped_relevant=False,
        matched=False,
    )


def compact_pieces(
    query: str,
    texts: Sequence[str],
    values: Sequence[float],
    budget: int,
    *,
    unit_scorer: Callable[[Sequence[str]], Sequence[float]] | None = None,
) -> CompactOutcome:
    """Compact the pooled chunks ``texts`` (page order) to ``budget`` tokens.

    ``values`` are the chunks' selection values, used only by the fallback.
    ``unit_scorer`` replaces BM25 with a score per unit text; headings stay at 0.
    """
    per_piece = [_units(md, i) for i, md in enumerate(texts)]
    units = [u for piece in per_piece for u in piece]
    own = (
        _scores(query, texts, units)
        if unit_scorer is None
        else _scored_by(unit_scorer, texts, units)
    )
    if not any(s > 0 for s in own):
        return _fallback(texts, values, budget)
    score_of = {(u.piece, u.index): s for u, s in zip(units, own, strict=True)}
    effective: dict[tuple[int, int], float] = {}
    for unit in units:
        neighbours = [
            score_of.get((unit.piece, unit.index + d), 0.0)
            for d in (-1, 1)
            if 0 <= unit.index + d < len(per_piece[unit.piece])
            and per_piece[unit.piece][unit.index + d].group == unit.group
        ]
        bonus = WINDOW_WEIGHT * sum(neighbours) if unit.kind != "heading" else 0.0
        effective[(unit.piece, unit.index)] = score_of[(unit.piece, unit.index)] + bonus
    tokens = {
        (u.piece, u.index): count_tokens(texts[u.piece][u.start : u.end]) for u in units
    }
    eligible = [u for u in units if effective[(u.piece, u.index)] > 0]
    eligible.sort(
        key=lambda u: (
            -effective[(u.piece, u.index)]
            / math.sqrt(max(tokens[(u.piece, u.index)], 1)),
            u.piece,
            u.index,
        )
    )
    kept: list[set[int]] = [set() for _ in texts]
    ranked: list[tuple[int, int]] = []  # kept units in pick order (headings too)
    spent = 0
    for unit in eligible:
        cost = tokens[(unit.piece, unit.index)]
        heading = unit.heading
        needs_heading = heading is not None and heading not in kept[unit.piece]
        if needs_heading and heading is not None:
            cost += tokens[(unit.piece, heading)]
        if spent + cost > budget:
            continue
        if needs_heading and heading is not None:
            kept[unit.piece].add(heading)
            ranked.append((unit.piece, heading))
        kept[unit.piece].add(unit.index)
        ranked.append((unit.piece, unit.index))
        spent += cost

    def rendered() -> list[str | None]:
        return [_render(md, per_piece[i], kept[i]) for i, md in enumerate(texts)]

    out = rendered()
    while ranked and sum(count_tokens(t) for t in out if t) > budget:
        piece, index = ranked.pop()
        kept[piece].discard(index)
        out = rendered()
    survivors = set(ranked)
    return CompactOutcome(
        texts=out,
        pruned=len(survivors) < len(units),
        dropped_relevant=any((u.piece, u.index) not in survivors for u in eligible),
    )


def compact_result(result: AssembleResult, *, query: str, budget: int) -> None:
    """Compact ``result`` (selected with :func:`pool_budget`) to ``budget``.

    Fragments keep their ids, provenance, scores and evidence (the evidence
    quote may lie in an omitted sentence: it still cites the chunk's blocks);
    ``md`` and ``tokens`` become the compacted text and its count.
    ``budget_tokens`` goes back to ``budget``; ``budget_limited`` also turns on
    when a unit that overlaps the query did not fit.
    """
    values = [
        f.scores.rerank if f.scores.rerank is not None else f.scores.rrf
        for f in result.fragments
    ]
    outcome = compact_pieces(query, [f.md for f in result.fragments], values, budget)
    fragments = []
    for fragment, text in zip(result.fragments, outcome.texts, strict=True):
        if text is not None:
            fragments.append(replace(fragment, md=text, tokens=count_tokens(text)))
    result.fragments = fragments
    result.output_tokens = sum(f.tokens for f in fragments)
    result.budget_tokens = budget
    result.budget_limited = result.budget_limited or outcome.dropped_relevant
    if not outcome.matched:
        result.compact = "no_match"
    else:
        result.compact = "pruned" if outcome.pruned else "whole"
