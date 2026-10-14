"""Reciprocal rank fusion (RRF) — pure, generic (SPEC §6 step 3).

RRF fuses several independently-ranked lists (here: lexical top-50 and vector
top-50) into one ranking. For a document ``d`` the fused score is

    score(d) = Σ_j  1 / (k + rank_j(d))

summed over the lists ``j`` in which ``d`` appears, where ``rank_j(d)`` is the
1-based position of ``d`` in list ``j`` and ``k`` is a smoothing constant
(``rrf_k = 60`` from config, SPEC §6). A document absent from a list contributes
nothing for that list. The constant ``k`` damps the influence of the very top
ranks so that agreement across lists matters more than a single list's lead.

This module is pure and generic over the document key type ``K`` (no I/O, no
dependency on the ORM): callers map their rows to hashable keys, fuse, then map
back. ``assemble`` passes keys ordered so that the deterministic tie-break is
meaningful: among documents with the exact same fused score, order is decided by
the key itself (callers use ``(document_id, ord)``), so the result is stable and
reproducible regardless of input list ordering.
"""

from __future__ import annotations

from collections.abc import Sequence


def rrf_fuse[K](
    ranked_lists: Sequence[Sequence[K]],
    *,
    k: int,
    weights: Sequence[float] | None = None,
) -> list[tuple[K, float]]:
    """Fuse ``ranked_lists`` with (optionally weighted) reciprocal rank fusion.

    Each inner sequence is a ranking (best first); element positions are the
    1-based ranks. Returns ``(key, fused_score)`` pairs sorted by fused score
    descending, with ties broken by the key ascending (callers pass keys ordered
    by ``(document_id, ord)`` so the tie-break is deterministic and meaningful).

    A key may appear in several lists; its score accumulates ``w_j / (k + rank)``
    once per list ``j``. A key absent from a list adds nothing for that list.

    ``weights`` gives a per-list multiplier ``w_j`` applied to that list's
    contribution: ``score(d) = Σ_j  w_j / (k + rank_j(d))``. ``None`` (the
    default) means every weight is ``1.0``, which reproduces plain RRF
    byte-for-byte. When given, ``weights`` MUST have one entry per ranked list
    (a mismatch raises :class:`ValueError`), so the M8 ``lexical_weight`` knob
    can bias the exact-term list without touching the vector list.
    """
    if weights is not None and len(weights) != len(ranked_lists):
        raise ValueError(
            f"weights length {len(weights)} does not match "
            f"ranked_lists length {len(ranked_lists)}"
        )
    scores: dict[K, float] = {}
    for list_index, ranked in enumerate(ranked_lists):
        weight = 1.0 if weights is None else weights[list_index]
        for position, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (k + position)
    # Sort by fused score desc, then by the key asc for a deterministic
    # tie-break. ``scores`` preserves first-seen insertion order, but the sort
    # key makes the result independent of it.
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
