"""Jina-compatible reranker on a LOCAL server (llama.cpp ``--reranking``).

``POST {base_url}/v1/rerank`` with ``{"model", "query", "documents", "top_n"}``
and reads ``results[{index, relevance_score}]`` — the body the Jina adapter
(:mod:`app.providers.rerank.jina`) already speaks, which llama.cpp's
``llama-server --reranking`` serves (research round 4, front I). The base URL
must be a loopback IP literal (:mod:`app.providers.local_endpoint`), and the
call uses its own ``httpx`` client, never the fetch guard (DECISIONS 2026-10-13
and 2026-10-29).

Scores come back in input order. ``score_transform="sigmoid"`` maps raw logits
to ``[0, 1]`` for servers or models that return them; otherwise the score is
used as it is (clamped downstream). Scores are rounded to 6 decimals so a
last-digit difference between runs cannot reorder ties (research I, I7).
Tests inject an ``httpx.MockTransport``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from app.providers.local_endpoint import local_client, validate_local_base_url

if TYPE_CHECKING:
    import httpx

ScoreTransform = Literal["none", "sigmoid"]


class LocalRerankError(RuntimeError):
    """The local rerank server answered with something unusable."""


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class JinaCompatibleRerankProvider:
    """Reranker backed by a local Jina-compatible ``/v1/rerank`` server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        score_transform: ScoreTransform = "none",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_local_base_url(base_url)
        self.model = model
        self.score_transform = score_transform
        self._transport = transport

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """One score per document, in input order."""
        if not documents:
            return []
        with local_client(self._transport) as client:
            response = client.post(
                f"{self.base_url}/v1/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                },
            )
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list):
            raise LocalRerankError("rerank server returned no 'results' list")
        scores: list[float | None] = [None] * len(documents)
        for item in results:
            raw = float(item["relevance_score"])
            if self.score_transform == "sigmoid":
                raw = _sigmoid(raw)
            scores[int(item["index"])] = round(raw, 6)
        if any(s is None for s in scores):
            raise LocalRerankError("rerank server skipped a document index")
        return [s for s in scores if s is not None]
