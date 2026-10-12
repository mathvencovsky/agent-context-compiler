"""Rerank stage: top-30 cap, normalized-RRF fallback, alignment (SPEC §6 step 4)."""

from __future__ import annotations

from app.providers.rerank.fake import FakeRerankProvider
from app.retrieve.rerank import rerank_candidates


def _fused(n: int) -> list[tuple[str, float]]:
    """n fused (key, score) pairs with strictly descending scores."""
    return [(f"k{i}", 1.0 - i * 0.01) for i in range(n)]


def test_top_30_cap() -> None:
    """Only the 30 best fused candidates are reranked."""
    fused = _fused(50)
    documents = {key: f"doc {key}" for key, _ in fused}
    out = rerank_candidates(
        "query",
        fused,
        provider=FakeRerankProvider(),
        use_rerank=True,
        documents=documents,
    )
    assert len(out) == 30
    assert [key for key, _, _ in out] == [f"k{i}" for i in range(30)]


def test_rerank_on_uses_provider_scores_in_range_and_aligned() -> None:
    """With rerank on, values are the (clamped) provider scores, aligned to input."""
    fused = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    documents = {
        "a": "authenticate api header token",
        "b": "unrelated prose about weather",
        "c": "api token header",
    }
    provider = FakeRerankProvider()
    expected = provider.rerank("api token header", list(documents.values()))

    out = rerank_candidates(
        "api token header",
        fused,
        provider=provider,
        use_rerank=True,
        documents=documents,
    )
    keys = [key for key, _, _ in out]
    assert keys == ["a", "b", "c"]  # input order preserved
    for (_, value, raw), exp in zip(out, expected, strict=True):
        assert 0.0 <= value <= 1.0
        assert raw == exp  # raw rerank score retained for scores.rerank
        assert value == exp


def test_rerank_off_uses_normalized_rrf_in_unit_range() -> None:
    """With rerank off, values are min-max normalized RRF in [0, 1], raw None."""
    fused = [("a", 0.030), ("b", 0.020), ("c", 0.010)]
    documents = {k: k for k, _ in fused}
    out = rerank_candidates(
        "query",
        fused,
        provider=FakeRerankProvider(),
        use_rerank=False,
        documents=documents,
    )
    values = {key: value for key, value, _ in out}
    assert values["a"] == 1.0  # max -> 1.0
    assert values["c"] == 0.0  # min -> 0.0
    assert 0.0 < values["b"] < 1.0
    assert all(raw is None for _, _, raw in out)


def test_rerank_off_single_candidate_normalizes_to_one() -> None:
    """A single candidate (no spread) normalizes to 1.0."""
    out = rerank_candidates(
        "query",
        [("only", 0.5)],
        provider=FakeRerankProvider(),
        use_rerank=False,
        documents={"only": "only"},
    )
    assert out == [("only", 1.0, None)]


def test_rerank_off_all_equal_normalizes_to_one() -> None:
    """An all-equal fused set normalizes every value to 1.0."""
    fused = [("a", 0.02), ("b", 0.02), ("c", 0.02)]
    out = rerank_candidates(
        "query",
        fused,
        provider=FakeRerankProvider(),
        use_rerank=False,
        documents={k: k for k, _ in fused},
    )
    assert all(value == 1.0 for _, value, _ in out)


def test_empty_fused_returns_empty() -> None:
    """No fused candidates -> no reranked output."""
    out = rerank_candidates(
        "query",
        [],
        provider=FakeRerankProvider(),
        use_rerank=True,
        documents={},
    )
    assert out == []
