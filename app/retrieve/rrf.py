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
    ranked_lists: Sequence[Sequence[K]], *, k: int
) -> list[tuple[K, float]]:
    """Fuse ``ranked_lists`` with reciprocal rank fusion.

    Each inner sequence is a ranking (best first); element positions are the
    1-based ranks. Returns ``(key, fused_score)`` pairs sorted by fused score
    descending, with ties broken by the key ascending (callers pass keys ordered
    by ``(document_id, ord)`` so the tie-break is deterministic and meaningful).

    A key may appear in several lists; its score accumulates ``1 / (k + rank)``
    once per list. A key absent from a list adds nothing for that list.
    """
    scores: dict[K, float] = {}
    for ranked in ranked_lists:
        for position, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position)
    # Sort by fused score desc, then by the key asc for a deterministic
    # tie-break. ``scores`` preserves first-seen insertion order, but the sort
    # key makes the result independent of it.
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
