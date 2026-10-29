"""Embeddings provider interface (SPEC §5).

Indexing turns each chunk's ``text`` into a dense vector stored in the pgvector
column (SPEC §5, §7). The provider is a synchronous seam mirroring
:mod:`app.providers.llm.base`: callers inject a provider and tests inject the
deterministic fake so no test touches the network (CLAUDE.md). The real adapter
(:mod:`app.providers.embeddings.openai`) is the only implementation that calls a
vendor API, and it is never used in tests.

Every returned vector has exactly ``dim`` components so the ORM ``Vector(dim)``
column (FEAT-002) and the HNSW index stay well-formed.

``embed`` embeds DOCUMENTS (chunks); ``embed_query`` embeds a search QUERY.
Symmetric models (OpenAI, the fake) embed both the same way, so their
``embed_query`` is ``embed([text])[0]``; asymmetric models (Qwen3-Embedding)
put an instruction in front of the query only (DECISIONS 2026-10-29). Callers
use :func:`embed_query`, which falls back to ``embed([text])[0]`` for a
provider (or wrapper) without the method.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Synchronous batch-embedding seam for indexing (SPEC §5).

    Implementations embed a batch of texts in one call. The output is a list
    aligned to ``texts`` where each vector has ``len == dim``. The fake is fully
    deterministic from the text content; the real adapter uses a fixed model and
    batches the request to the vendor API.
    """

    #: Stable model identifier (e.g. ``"text-embedding-3-small"``).
    model: str

    #: Dimensionality of every returned vector (e.g. ``1536``).
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` and return one ``dim``-length vector per input."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (``embed([text])[0]`` for symmetric models)."""
        ...


def embed_query(provider: object, text: str) -> list[float]:
    """``provider.embed_query(text)``, or ``provider.embed([text])[0]`` without it."""
    method = getattr(provider, "embed_query", None)
    if callable(method):
        return list(method(text))
    return list(provider.embed([text])[0])  # type: ignore[attr-defined]
