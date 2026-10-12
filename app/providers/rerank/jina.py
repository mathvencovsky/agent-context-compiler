"""Real Jina reranker provider (SPEC §6) — never exercised by tests.

The reranker model defaults to ``jina-reranker-v2-base-multilingual`` (human
decision; see .env.example / app.config). ``httpx`` is imported lazily inside
:meth:`rerank` so merely constructing the provider — or importing this module in
a test — needs neither httpx configured nor a key. This uses its own ``httpx``
client (NOT the fetch guard): Jina is a trusted provider, not untrusted page
content (recorded in DECISIONS.md as part of FEAT-003 docs). The key is read
from ``settings.reranker_api_key``; when it is unset :meth:`rerank` raises
:class:`MissingAPIKey`. Tests use
:class:`~app.providers.rerank.fake.FakeRerankProvider`.
"""

from __future__ import annotations

from app.config import Settings, get_settings

# Reuse the shared missing-key error so callers catch one type across providers.
from app.providers.llm.openai import MissingAPIKey

__all__ = ["JinaRerankProvider", "MissingAPIKey"]

#: Jina reranker endpoint (SPEC §6).
_RERANK_URL = "https://api.jina.ai/v1/rerank"

#: Request timeout in seconds for the rerank call.
_TIMEOUT_SECONDS = 30.0


class JinaRerankProvider:
    """Reranker backed by the Jina API (``jina-reranker-v2-base-multilingual``)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self.model = model if model is not None else self._settings.reranker_model

    def _api_key(self) -> str:
        """Resolve the Jina reranker key or fail loudly."""
        key = self._settings.reranker_api_key
        if not key:
            raise MissingAPIKey(
                "RERANKER_API_KEY is not set; the Jina reranker provider requires "
                "it. Set it in .env for the real run, or use FakeRerankProvider "
                "in tests."
            )
        return key

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against ``query`` via the Jina rerank API.

        Returns one relevance score per document IN INPUT ORDER. The vendor
        returns results by ``index`` (not necessarily input order); scores are
        reordered to input order and min-max normalized to ``[0, 1]`` (a single
        document, or all-equal scores, normalizes to ``1.0``).
        """
        if not documents:
            return []

        api_key = self._api_key()

        # Lazy import: keep httpx off the import path for construction/tests.
        import httpx

        response = httpx.post(
            _RERANK_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        results = response.json()["results"]

        # Reorder vendor results (keyed by index) back into input order.
        raw_scores: list[float] = [0.0] * len(documents)
        for item in results:
            raw_scores[item["index"]] = float(item["relevance_score"])

        return _min_max_normalize(raw_scores)


def _min_max_normalize(scores: list[float]) -> list[float]:
    """Min-max scale ``scores`` to ``[0, 1]``; single/all-equal -> all ``1.0``."""
    lo = min(scores)
    hi = max(scores)
    span = hi - lo
    if span == 0.0:
        return [1.0] * len(scores)
    return [(s - lo) / span for s in scores]
