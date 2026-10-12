"""Real OpenAI embeddings provider (SPEC §5) — never exercised by tests.

The embeddings model is ``text-embedding-3-small`` (dim 1536; human decision,
DECISIONS.md 2026-10-12). The OpenAI SDK is imported lazily inside
:meth:`embed` so merely constructing the provider — or importing this module in
a test — needs neither the SDK configured nor a key. The key is read from
``settings.embeddings_api_key`` and falls back to ``settings.bench_answerer_api_key``
(the OpenAI answerer key) so no new key is required; when neither is set
:meth:`embed` raises :class:`MissingAPIKey`.

The whole batch is embedded in a single ``client.embeddings.create`` call,
wrapped in an exponential-backoff retry (:data:`_MAX_ATTEMPTS` attempts, base
delay :data:`_BASE_DELAY_SECONDS`) because transient API errors are common on
batched calls. Tests use :class:`~app.providers.embeddings.fake.FakeEmbeddingProvider`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, TypeVar

from app.config import Settings, get_settings

# Reuse the shared missing-key error so callers catch one type across providers.
from app.providers.llm.openai import MissingAPIKey

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["MissingAPIKey", "OpenAIEmbeddingProvider"]

#: Retry policy for the batched embeddings call (exponential backoff).
_MAX_ATTEMPTS = 3
_BASE_DELAY_SECONDS = 0.5

_T = TypeVar("_T")


class OpenAIEmbeddingProvider:
    """Embeddings backed by the OpenAI API (``text-embedding-3-small``)."""

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
        settings: Settings | None = None,
    ) -> None:
        self.model = model
        self.dim = dim
        self._settings = settings if settings is not None else get_settings()

    def _api_key(self) -> str:
        """Resolve the OpenAI key: embeddings key, else the answerer key."""
        key = self._settings.embeddings_api_key or self._settings.bench_answerer_api_key
        if not key:
            raise MissingAPIKey(
                "No OpenAI key for embeddings: set EMBEDDINGS_API_KEY, or reuse "
                "the answerer key via BENCH_ANSWERER_API_KEY. The real embeddings "
                "provider requires one; use FakeEmbeddingProvider in tests."
            )
        return key

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` in one batched API call with retry/backoff."""
        api_key = self._api_key()

        # Lazy import: keep the SDK off the import path for construction/tests.
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = self._with_retry(
            lambda: client.embeddings.create(model=self.model, input=texts)
        )
        return [list(item.embedding) for item in response.data]

    def _with_retry(self, call: Callable[[], _T]) -> _T:
        """Run ``call`` with exponential backoff on transient failures.

        Retries up to :data:`_MAX_ATTEMPTS` times, sleeping
        ``_BASE_DELAY_SECONDS * 2**attempt`` between tries; the last failure is
        re-raised unchanged.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 — re-raised after retries
                last_exc = exc
                if attempt == _MAX_ATTEMPTS - 1:
                    break
                time.sleep(_BASE_DELAY_SECONDS * (2**attempt))
        assert last_exc is not None  # loop ran at least once
        raise last_exc
