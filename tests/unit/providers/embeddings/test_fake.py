"""FakeEmbeddingProvider: interface, shape, unit norm and determinism."""

from __future__ import annotations

import math

from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.fake import FakeEmbeddingProvider


def test_satisfies_protocol() -> None:
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


def test_vectors_have_dim_length() -> None:
    provider = FakeEmbeddingProvider(dim=1536)
    vecs = provider.embed(["hello", "world", ""])
    assert len(vecs) == 3
    assert all(len(v) == 1536 for v in vecs)


def test_vectors_are_unit_norm() -> None:
    provider = FakeEmbeddingProvider(dim=64)
    for vec in provider.embed(["a", "some longer text", "another"]):
        assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_empty_text_is_unit_norm() -> None:
    # sha256("") is non-zero so this is already unit norm, but assert the
    # contract holds for the empty input too.
    (vec,) = FakeEmbeddingProvider(dim=32).embed([""])
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_deterministic_across_calls() -> None:
    provider = FakeEmbeddingProvider()
    first = provider.embed(["alpha", "beta"])
    second = provider.embed(["alpha", "beta"])
    assert first == second


def test_distinct_texts_differ() -> None:
    provider = FakeEmbeddingProvider(dim=128)
    a, b = provider.embed(["alpha", "beta"])
    assert a != b
