"""Rerank stage — top-30, normalize to [0, 1] (SPEC §6 step 4).

The fused RRF ranking (SPEC §6 step 3) is trimmed to its top-30 and each
surviving candidate is assigned the ``value`` the greedy selector will use:

- ``use_rerank=True``: the reranker scores every candidate's document text
  against the query. The provider already returns scores in ``[0, 1]`` in input
  order (:class:`~app.providers.rerank.base.RerankProvider`); we clamp
  defensively and use that as the value, keeping the raw score for
  ``scores.rerank``.
- ``use_rerank=False`` (ablation): the provider is skipped and the value is the
  min-max NORMALIZED RRF score over the top-30. A single candidate — or an
  all-equal set — normalizes to ``1.0``. ``rerank_score`` is ``None`` in this
  mode (there is nothing to report for ``scores.rerank``).

Pure given the provider: no I/O beyond the injected provider, which is the
deterministic fake in every test (CLAUDE.md). Output is aligned to the trimmed
input order, one ``(key, value, rerank_score|None)`` per candidate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.rerank.base import RerankProvider

#: SPEC §6 step 4: rerank the 30 best of the fused ranking.
_DEFAULT_TOP_N = 30


def _clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0, 1]`` (defensive against provider drift)."""
    return max(0.0, min(1.0, value))


def _normalize_rrf(scores: list[float]) -> list[float]:
    """Min-max normalize fused scores to ``[0, 1]``.

    A single element or an all-equal set has no spread, so every value maps to
    ``1.0`` (the fragments are equally the best available).
    """
    if not scores:
        return []
    lo = min(scores)
    hi = max(scores)
    if hi == lo:
        return [1.0 for _ in scores]
    span = hi - lo
    return [(s - lo) / span for s in scores]


def rerank_candidates[K](
    query: str,
    fused: list[tuple[K, float]],
    *,
    provider: RerankProvider,
    use_rerank: bool,
    documents: dict[K, str],
    top_n: int = _DEFAULT_TOP_N,
) -> list[tuple[K, float, float | None]]:
    """Assign a selection value to the top-``top_n`` fused candidates.

    ``fused`` is the RRF output (``(key, fused_score)`` sorted best-first).
    ``documents`` maps each key to the text handed to the reranker. Returns one
    ``(key, value, rerank_score|None)`` per candidate, in the trimmed input
    order, where ``value`` is the clamped rerank score (``use_rerank=True``) or
    the normalized RRF score (``use_rerank=False``).
    """
    top = fused[:top_n]
    if not top:
        return []

    if use_rerank:
        texts = [documents[key] for key, _ in top]
        raw_scores = provider.rerank(query, texts)
        return [
            (key, _clamp01(score), score)
            for (key, _), score in zip(top, raw_scores, strict=True)
        ]

    normalized = _normalize_rrf([fused_score for _, fused_score in top])
    return [(key, value, None) for (key, _), value in zip(top, normalized, strict=True)]
