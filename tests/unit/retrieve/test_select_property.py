"""Property test: selection never exceeds the budget (SPEC §6 step 5)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.retrieve.select import Candidate, select

LAMBDA = 0.5
MIN_VALUE = 0.05

_candidates = st.lists(
    st.builds(
        Candidate,
        chunk_id=st.integers(min_value=0, max_value=10_000),
        document_id=st.sampled_from(["doc_a", "doc_b", "doc_c"]),
        ord=st.integers(min_value=0, max_value=100),
        value=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        tokens=st.integers(min_value=1, max_value=800),
        embedding=st.lists(
            st.floats(
                min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False
            ),
            min_size=3,
            max_size=3,
        ),
    ),
    max_size=25,
)


@given(candidates=_candidates, budget=st.integers(min_value=256, max_value=32_000))
def test_output_tokens_never_exceed_budget(
    candidates: list[Candidate], budget: int
) -> None:
    """The sum of selected tokens is always within the budget, in every case."""
    selected = select(
        candidates, budget_tokens=budget, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert sum(c.tokens for c in selected) <= budget


# Candidates whose embeddings all point the SAME way: ``max_sim`` is then
# non-negative (cosine of equal directions is 1.0), so the diversity penalty can
# only LOWER the adjusted value. Under that constraint the raw ``value`` is an
# upper bound on the adjusted value, so nothing below ``min_value`` is selectable.
_same_direction_candidates = st.lists(
    st.builds(
        Candidate,
        chunk_id=st.integers(min_value=0, max_value=10_000),
        document_id=st.sampled_from(["doc_a", "doc_b", "doc_c"]),
        ord=st.integers(min_value=0, max_value=100),
        value=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        tokens=st.integers(min_value=1, max_value=800),
        embedding=st.just([1.0, 0.0, 0.0]),
    ),
    max_size=25,
)


@given(
    candidates=_same_direction_candidates,
    budget=st.integers(min_value=256, max_value=32_000),
)
def test_nothing_below_min_value_is_selected(
    candidates: list[Candidate], budget: int
) -> None:
    """With a non-negative diversity penalty, raw value bounds the adjusted value.

    The penalty is ``λ · max_sim`` with ``max_sim >= 0`` here (identical
    directions), and ``sqrt(tokens) >= 1`` for ``tokens >= 1``, so the adjusted
    value never exceeds the raw ``value``. A candidate selected only when its
    adjusted value clears ``min_value`` therefore has raw ``value >= min_value``.
    """
    selected = select(
        candidates, budget_tokens=budget, lambda_=LAMBDA, min_value=MIN_VALUE
    )
    assert all(c.value >= MIN_VALUE for c in selected)
