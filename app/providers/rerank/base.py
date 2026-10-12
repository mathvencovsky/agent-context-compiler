"""Rerank provider interface (SPEC §6).

Reranking scores each candidate chunk against the query so selection can favour
the most relevant fragments. The provider is a synchronous seam mirroring
:mod:`app.providers.embeddings.base`: callers inject a provider and tests inject
the deterministic :class:`~app.providers.rerank.fake.FakeRerankProvider` so no
test touches the network (CLAUDE.md). The real adapter
(:mod:`app.providers.rerank.jina`) is the only implementation that calls a
vendor API, and it is never used in tests.

Every returned list is aligned to ``documents`` (one score per input, in input
order) with each score in ``[0, 1]``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RerankProvider(Protocol):
    """Synchronous query/document reranking seam (SPEC §6).

    Implementations score a batch of ``documents`` against a single ``query``.
    The output is a list aligned to ``documents`` where each score is in
    ``[0, 1]`` and the i-th score corresponds to the i-th input document (input
    order is preserved — the provider reorders internally if the vendor returns
    results by relevance). The fake is fully deterministic from the text; the
    real adapter uses a fixed model and calls the vendor API.
    """

    #: Stable model identifier (e.g. ``"jina-reranker-v2-base-multilingual"``).
    model: str

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against ``query``.

        Returns one relevance score in ``[0, 1]`` per document, IN INPUT ORDER
        (``result[i]`` is the score for ``documents[i]``).
        """
        ...
