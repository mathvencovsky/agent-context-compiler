"""Deterministic fake rerank provider — backs every test (CLAUDE.md).

The fake scores each document by the lexical token overlap between the query and
the document: it lowercases both, extracts alphanumeric tokens, and takes the
Jaccard ratio ``|q ∩ d| / |q ∪ d|`` of the token sets. The result is already in
``[0, 1]`` (clamped defensively); it is fully deterministic from the text and
never touches the network. Disjoint query/document pairs score ``0.0`` and an
exact token match scores ``1.0``.
"""

from __future__ import annotations

import re

#: Tokenizer: runs of lowercase alphanumeric characters.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class FakeRerankProvider:
    """Deterministic, network-free rerank provider for tests."""

    def __init__(self, *, model: str = "fake-rerank") -> None:
        self.model = model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one deterministic overlap score per document, in input order."""
        query_tokens = _tokenize(query)
        return [self._score(query_tokens, doc) for doc in documents]

    def _score(self, query_tokens: frozenset[str], document: str) -> float:
        doc_tokens = _tokenize(document)
        union = query_tokens | doc_tokens
        if not union:
            # Both empty: no signal either way.
            return 0.0
        overlap = len(query_tokens & doc_tokens)
        score = overlap / len(union)
        # Defensive clamp; the Jaccard ratio is already in [0, 1].
        return max(0.0, min(1.0, score))


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase ``text`` and return its set of alphanumeric tokens."""
    return frozenset(_TOKEN_RE.findall(text.lower()))
