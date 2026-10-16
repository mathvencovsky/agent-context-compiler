"""Greedy budget-constrained selection (SPEC §6 steps 5-6)."""

from __future__ import annotations

from app.retrieve.select import Candidate, cosine, select

LAMBDA = 0.5
MIN_VALUE = 0.05


def _cand(
    *,
    chunk_id: int,
    document_id: str,
    ord: int,
    value: float,
    tokens: int,
    embedding: list[float],
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        document_id=document_id,
        ord=ord,
        value=value,
        tokens=tokens,
        embedding=embedding,
    )


def test_cosine_is_numerically_safe() -> None:
    """Zero-norm vectors yield 0.0; identical vectors yield 1.0."""
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_budget_cap_is_respected() -> None:
    """Selected tokens never exceed the budget."""
    # Each chunk is high-value (0.9 / sqrt(100) = 0.09 > min_value) with a
    # distinct one-hot embedding so there is no near-duplicate penalty.
    candidates = [
        _cand(
            chunk_id=i,
            document_id="doc_a",
            ord=i,
            value=0.9,
            tokens=100,
            embedding=[1.0 if j == i else 0.0 for j in range(10)],
        )
        for i in range(10)
    ]
    selected = select(
        candidates, budget_tokens=250, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert sum(c.tokens for c in selected) <= 250
    # 250 // 100 = 2 chunks fit.
    assert len(selected) == 2


def test_near_duplicate_is_penalized() -> None:
    """A near-identical high-value chunk loses to a distinct alternative.

    Two chunks share the SAME embedding as the first-picked chunk (near-dups),
    and one chunk points in an orthogonal direction (distinct). With a budget
    that fits exactly two chunks, the distinct one must be chosen second.
    """
    budget = 200  # two 100-token chunks fit
    anchor_emb = [1.0, 0.0]
    dup_emb = [1.0, 0.0]  # cosine 1.0 with anchor -> heavily penalized
    distinct_emb = [0.0, 1.0]  # cosine 0.0 with anchor -> no penalty

    candidates = [
        _cand(
            chunk_id=1,
            document_id="doc_a",
            ord=0,
            value=0.95,
            tokens=100,
            embedding=anchor_emb,
        ),
        _cand(
            chunk_id=2,
            document_id="doc_a",
            ord=1,
            value=0.90,
            tokens=100,
            embedding=dup_emb,
        ),
        _cand(
            chunk_id=3,
            document_id="doc_b",
            ord=0,
            value=0.70,
            tokens=100,
            embedding=distinct_emb,
        ),
    ]
    selected = select(
        candidates, budget_tokens=budget, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    chosen_ids = {c.chunk_id for c in selected}
    # Anchor (highest value) and the distinct chunk, NOT the near-duplicate.
    assert chosen_ids == {1, 3}


def test_select_all_above_min_value_with_large_budget() -> None:
    """A large budget selects every candidate whose adjusted value clears min."""
    candidates = [
        _cand(
            chunk_id=i,
            document_id="doc_a",
            ord=i,
            value=0.8,
            tokens=100,
            embedding=[1.0 if j == i else 0.0 for j in range(5)],
        )
        for i in range(5)
    ]
    selected = select(
        candidates, budget_tokens=100_000, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert len(selected) == 5


def test_stops_below_min_value() -> None:
    """A candidate whose adjusted value is below min_value is never selected."""
    candidates = [
        _cand(
            chunk_id=1,
            document_id="doc_a",
            ord=0,
            value=0.01,  # 0.01 / sqrt(100) = 0.001 < min_value
            tokens=100,
            embedding=[1.0, 0.0],
        ),
    ]
    selected = select(
        candidates, budget_tokens=10_000, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert selected == []


def test_output_is_ordered_by_document_then_ord() -> None:
    """Survivors come back ordered by (document_id, ord), not by value."""
    candidates = [
        _cand(
            chunk_id=1,
            document_id="doc_b",
            ord=0,
            value=0.9,
            tokens=100,
            embedding=[1.0, 0.0, 0.0],
        ),
        _cand(
            chunk_id=2,
            document_id="doc_a",
            ord=5,
            value=0.8,
            tokens=100,
            embedding=[0.0, 1.0, 0.0],
        ),
        _cand(
            chunk_id=3,
            document_id="doc_a",
            ord=1,
            value=0.7,
            tokens=100,
            embedding=[0.0, 0.0, 1.0],  # orthogonal: no near-dup penalty
        ),
    ]
    selected = select(
        candidates, budget_tokens=10_000, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert [(c.document_id, c.ord) for c in selected] == [
        ("doc_a", 1),
        ("doc_a", 5),
        ("doc_b", 0),
    ]


def test_frozen_config_fills_the_budget_with_related_chunks() -> None:
    """M10a regression: the frozen λ must not end selection with budget left.

    With λ=0.3 a lower-value chunk that resembles one already chosen scored
    below ``min_value`` and stopped the loop; on real docs tasks that dropped
    the answer section (DECISIONS.md 2026-10-16).
    """
    from app.config import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    pool = [
        _cand(chunk_id=0, document_id="d", ord=0, value=1.0, tokens=100,
              embedding=[1.0, 0.0]),
        _cand(chunk_id=1, document_id="d", ord=1, value=0.2, tokens=100,
              embedding=[1.0, 0.0]),
    ]  # fmt: skip
    got = select(
        pool,
        budget_tokens=1_000,
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
    )
    assert [c.chunk_id for c in got] == [0, 1]
