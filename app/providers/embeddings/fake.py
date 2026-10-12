"""Deterministic fake embeddings provider — backs every test (CLAUDE.md).

The fake derives each vector purely from ``sha256(text.encode())`` so two calls
with the same input return byte-identical results (determinism) and never touch
the network. The 32-byte digest is repeated until ``dim`` bytes are available,
each byte is mapped to a float in ``[0, 1)`` (``byte / 256``), and the vector is
L2-normalized to unit norm. A degenerate all-zero vector (only possible if every
sampled byte is 0) is handled by setting the first component to ``1.0`` so the
norm is always exactly 1.
"""

from __future__ import annotations

import hashlib
import math

#: Default embedding dimensionality (OpenAI text-embedding-3-small; SPEC §5).
_DEFAULT_DIM = 1536


class FakeEmbeddingProvider:
    """Deterministic, network-free embeddings provider for tests."""

    def __init__(self, *, model: str = "fake-embed", dim: int = _DEFAULT_DIM) -> None:
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one deterministic unit vector per input text."""
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        # Repeat the 32-byte digest until we have at least ``dim`` bytes.
        raw = (digest * (self.dim // len(digest) + 1))[: self.dim]
        values = [b / 256.0 for b in raw]
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            # Degenerate all-zero vector: pin the first component so norm == 1.
            values[0] = 1.0
            return values
        return [v / norm for v in values]
