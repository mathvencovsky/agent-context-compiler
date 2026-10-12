"""Embeddings provider interface (SPEC §5).

Indexing turns each chunk's ``text`` into a dense vector stored in the pgvector
column (SPEC §5, §7). The provider is a synchronous seam mirroring
:mod:`app.providers.llm.base`: callers inject a provider and tests inject the
deterministic fake so no test touches the network (CLAUDE.md). The real adapter
(:mod:`app.providers.embeddings.openai`) is the only implementation that calls a
vendor API, and it is never used in tests.

Every returned vector has exactly ``dim`` components so the ORM ``Vector(dim)``
column (FEAT-002) and the HNSW index stay well-formed.
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
