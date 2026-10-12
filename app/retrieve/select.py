"""Budget-constrained greedy selection — pure (SPEC §6 steps 5-6).

Given scored candidate chunks and a token budget ``B = max_tokens``, selection
greedily builds the output set. At each step it picks the candidate ``i`` that
maximizes the adjusted value

    (V_i - λ · max_sim(i, selected)) / sqrt(t_i)

where ``V_i`` is the candidate value (rerank score, or normalized RRF when
rerank is off), ``t_i`` is the chunk's token count, and ``max_sim(i, selected)``
is the maximum cosine similarity between candidate ``i``'s embedding and the
embedding of any already-selected candidate (``0`` when nothing is selected
yet). The ``λ`` term penalizes near-duplicates of what is already selected, so
the output favors diverse, high-value fragments. ``sqrt(t_i)`` biases toward
shorter chunks per token spent.

The loop skips candidates that do not fit the remaining budget and stops when
nothing fits OR when the best adjusted value falls below ``min_value``. The
survivors are returned ordered by ``(document_id, ord)`` so a reader follows the
documents in order (SPEC §6 step 6).

Pure and deterministic: no I/O, no LLM. ``λ`` and ``min_value`` come from config
(``selection_lambda = 0.5``, ``min_value = 0.05``; SPEC §6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """One selectable chunk with everything the selector and output need.

    ``value`` is ``V_i`` (rerank score or normalized RRF). ``tokens`` is ``t_i``.
    ``embedding`` is the chunk's dense vector (for the diversity penalty).
    ``document_id`` and ``ord`` drive the SPEC §6 step 6 output ordering, and
    ``chunk_id`` carries the DB identity through to the output fragment.
    """

    chunk_id: int
    document_id: str
    ord: int
    value: float
    tokens: int
    embedding: list[float]


def cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two vectors, numerically safe.

    A zero-norm vector (no direction) has no meaningful similarity, so this
    returns ``0.0`` instead of dividing by zero. Vectors of differing lengths
    are compared over their shared prefix (defensive; equal lengths are the
    norm). The result is clamped to ``[-1, 1]`` to absorb floating-point drift.
    """
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _max_sim(candidate: Candidate, selected: list[Candidate]) -> float:
    """Max cosine between ``candidate`` and any already-selected embedding."""
    if not selected:
        return 0.0
    return max(cosine(candidate.embedding, s.embedding) for s in selected)


def _adjusted_value(
    candidate: Candidate, selected: list[Candidate], lambda_: float
) -> float:
    """The SPEC §6 adjusted value ``(V_i - λ·max_sim) / sqrt(t_i)``.

    A candidate with zero (or non-positive) tokens cannot be scored by the
    formula; it is treated as unselectable (``-inf``) so it never wins a step.
    """
    if candidate.tokens <= 0:
        return float("-inf")
    penalty = lambda_ * _max_sim(candidate, selected)
    return (candidate.value - penalty) / math.sqrt(candidate.tokens)


def select(
    candidates: list[Candidate],
    *,
    budget_tokens: int,
    lambda_: float,
    min_value: float,
) -> list[Candidate]:
    """Greedily select candidates under a token budget (SPEC §6 steps 5-6).

    At each step the candidate with the highest adjusted value that still fits
    the remaining budget is chosen. The loop stops when nothing fits or when the
    best adjusted value is below ``min_value``. The selected candidates are
    returned ordered by ``(document_id, ord)``.
    """
    remaining = list(candidates)
    selected: list[Candidate] = []
    budget_left = budget_tokens

    while remaining:
        # Only candidates that still fit the remaining budget are eligible.
        fitting = [c for c in remaining if 0 < c.tokens <= budget_left]
        if not fitting:
            break

        best = max(fitting, key=lambda c: _adjusted_value(c, selected, lambda_))
        best_value = _adjusted_value(best, selected, lambda_)
        if best_value < min_value:
            break

        selected.append(best)
        budget_left -= best.tokens
        remaining.remove(best)

    selected.sort(key=lambda c: (c.document_id, c.ord))
    return selected
