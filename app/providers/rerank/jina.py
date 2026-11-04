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

import time
from typing import TYPE_CHECKING

from app.config import Settings, get_settings

if TYPE_CHECKING:
    import httpx

# Reuse the shared missing-key error so callers catch one type across providers.
from app.providers.llm.openai import MissingAPIKey, require_paid_calls

__all__ = ["JinaRerankProvider", "MissingAPIKey"]

#: Jina reranker endpoint (SPEC §6).
_RERANK_URL = "https://api.jina.ai/v1/rerank"

#: Request timeout in seconds for the rerank call.
_TIMEOUT_SECONDS = 30.0

#: Retry policy for transient failures (HTTP 429 / 5xx). The real run makes many
#: sequential calls and the vendor rate-limits; retry with exponential backoff
#: (honoring Retry-After when present) rather than aborting a whole run.
_MAX_ATTEMPTS = 5
_BASE_DELAY_SECONDS = 2.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


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
        require_paid_calls(self._settings, "the Jina reranker")
        return key

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against ``query`` via the Jina rerank API.

        Returns one ABSOLUTE relevance score per document IN INPUT ORDER. The
        vendor returns results by ``index`` (not necessarily input order), so
        scores are reordered to input order. The raw ``relevance_score`` is
        returned verbatim (clamped to ``[0, 1]`` defensively) — NOT min-max
        normalized per call. Absolute scores are comparable ACROSS calls/tasks,
        which the coverage heuristic (``assemble._coverage``) requires; the
        per-candidate min-max normalization that the greedy selector uses as its
        value lives in ``app.retrieve.rerank.rerank_candidates`` instead, so the
        selection behavior is unchanged while coverage gets a meaningful,
        cross-task-comparable observable (DECISIONS.md 2026-10-13 — the per-call
        normalization previously here made every task's max score 1.0, breaking
        coverage).
        """
        if not documents:
            return []

        api_key = self._api_key()

        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        response = self._post_with_retry(
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        results = response.json()["results"]

        # Reorder vendor results (keyed by index) back into input order and keep
        # the ABSOLUTE relevance score (defensively clamped to [0, 1]).
        raw_scores: list[float] = [0.0] * len(documents)
        for item in results:
            raw_scores[item["index"]] = _clamp01(float(item["relevance_score"]))

        return raw_scores

    def _post_with_retry(
        self,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> httpx.Response:
        """POST to the rerank endpoint, retrying on 429/5xx with backoff.

        Honors a ``Retry-After`` header when the vendor sends one, else sleeps
        ``_BASE_DELAY_SECONDS * 2**attempt``. The last response is returned (its
        ``raise_for_status`` surfaces a persistent failure to the caller) so a
        genuine, non-transient error is never swallowed.
        """
        # Lazy import: keep httpx off the import path for construction/tests.
        import httpx

        last_response: httpx.Response | None = None
        for attempt in range(_MAX_ATTEMPTS):
            last_response = httpx.post(
                _RERANK_URL, headers=headers, json=json, timeout=_TIMEOUT_SECONDS
            )
            if last_response.status_code not in _RETRY_STATUS:
                return last_response
            if attempt == _MAX_ATTEMPTS - 1:
                break
            time.sleep(_retry_after(last_response, _BASE_DELAY_SECONDS * (2**attempt)))
        assert last_response is not None  # loop ran at least once
        return last_response


def _retry_after(response: httpx.Response, fallback: float) -> float:
    """Return the ``Retry-After`` delay in seconds, else ``fallback``."""
    value = response.headers.get("Retry-After")
    if value is None:
        return fallback
    try:
        return max(fallback, float(value))
    except ValueError:
        return fallback


def _clamp01(value: float) -> float:
    """Clamp ``value`` into ``[0, 1]`` (defensive against vendor drift)."""
    return max(0.0, min(1.0, value))
