"""Unit tests for the M8 calibration LOGIC (synthetic data, no network).

Only the PURE functions are exercised here — :func:`pick_best` (winner
selection, D6), :func:`percentiles` / :func:`coverage_from_scores` (coverage
derivation, D7) and the :data:`GRID` constant. The real calibration path
(``run_calibration``) constructs network providers and is a human-run utility,
never collected by pytest; nothing in this file touches the network or builds a
real provider (CLAUDE.md).
"""

from __future__ import annotations

import pytest

from bench.calibrate import (
    GRID,
    LEXICAL_WEIGHT_GRID,
    CombinationResult,
    LexicalWeightResult,
    coverage_from_scores,
    percentiles,
    pick_best,
    pick_best_lexical_weight,
)


def _combo(
    lambda_: float,
    min_value: float,
    *,
    success_rate: float,
    cost: float = 1.0,
    ctx: float = 100.0,
) -> CombinationResult:
    return CombinationResult(
        lambda_=lambda_,
        min_value=min_value,
        n_tasks=96,
        success_rate=success_rate,
        cost_per_completed_task=cost,
        mean_context_tokens=ctx,
    )


def test_grid_is_the_nine_expected_combinations() -> None:
    """GRID is exactly lambda {0.3,0.5,0.7} x min_value {0.0,0.05,0.1}."""
    expected = [
        (lambda_, min_value)
        for lambda_ in (0.3, 0.5, 0.7)
        for min_value in (0.0, 0.05, 0.1)
    ]
    assert GRID == expected
    assert len(GRID) == 9


def test_pick_best_prefers_highest_success_rate() -> None:
    """Primary key: the combination with the highest success rate wins."""
    results = [
        _combo(0.3, 0.0, success_rate=0.50),
        _combo(0.5, 0.05, success_rate=0.70),
        _combo(0.7, 0.1, success_rate=0.60),
    ]
    best = pick_best(results)
    assert (best.lambda_, best.min_value) == (0.5, 0.05)


def test_pick_best_cost_breaks_success_tie() -> None:
    """Tie-break 1: equal success -> lowest cost per completed task wins."""
    results = [
        _combo(0.3, 0.0, success_rate=0.70, cost=2.0),
        _combo(0.5, 0.05, success_rate=0.70, cost=1.0),
    ]
    best = pick_best(results)
    assert (best.lambda_, best.min_value) == (0.5, 0.05)


def test_pick_best_ctx_tokens_breaks_success_and_cost_tie() -> None:
    """Tie-break 2: equal success+cost -> lowest mean context tokens wins."""
    results = [
        _combo(0.3, 0.0, success_rate=0.70, cost=1.0, ctx=200.0),
        _combo(0.7, 0.1, success_rate=0.70, cost=1.0, ctx=120.0),
    ]
    best = pick_best(results)
    assert (best.lambda_, best.min_value) == (0.7, 0.1)


def test_pick_best_final_tie_is_deterministic() -> None:
    """Final tie-break: all metrics equal -> lowest (lambda_, min_value) wins."""
    results = [
        _combo(0.7, 0.1, success_rate=0.70, cost=1.0, ctx=100.0),
        _combo(0.3, 0.0, success_rate=0.70, cost=1.0, ctx=100.0),
        _combo(0.5, 0.05, success_rate=0.70, cost=1.0, ctx=100.0),
    ]
    best = pick_best(results)
    assert (best.lambda_, best.min_value) == (0.3, 0.0)
    # Re-ordering the input must not change the deterministic winner.
    assert pick_best(list(reversed(results))) == best


def test_pick_best_empty_raises() -> None:
    """An empty result list has no winner to pick."""
    with pytest.raises(ValueError, match="at least one"):
        pick_best([])


def test_percentiles_ordered_on_uniform_ramp() -> None:
    """p33 <= p66 and both land inside the observed range (nearest-rank)."""
    scores = [i / 10 for i in range(11)]  # 0.0 .. 1.0
    pct = percentiles(scores, (0.33, 0.66))
    assert pct["p33"] <= pct["p66"]
    assert 0.0 <= pct["p33"] <= 1.0
    assert 0.0 <= pct["p66"] <= 1.0


def test_percentiles_empty_is_zero() -> None:
    """An empty score list maps every requested percentile to 0.0."""
    assert percentiles([], (0.33, 0.66)) == {"p33": 0.0, "p66": 0.0}


def test_coverage_from_scores_returns_ordered_thresholds() -> None:
    """coverage_from_scores returns (p33, p66) with low <= high."""
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    low, high = coverage_from_scores(scores)
    assert low <= high
    assert low in scores
    assert high in scores


def _lw(
    lexical_weight: float,
    *,
    success_rate: float,
    cost: float = 1.0,
    ctx: float = 100.0,
) -> LexicalWeightResult:
    return LexicalWeightResult(
        lexical_weight=lexical_weight,
        n_tasks=96,
        success_rate=success_rate,
        cost_per_completed_task=cost,
        mean_context_tokens=ctx,
    )


def test_lexical_weight_grid_shape_and_identity_first() -> None:
    """The dev-only lexical_weight grid is the four expected points, 1.0 first.

    1.0 (the identity) must be in the grid so the sweep can reproduce today's
    behavior, and it is the smallest value so the pick_best tie-break defaults
    to it.
    """
    assert LEXICAL_WEIGHT_GRID == (1.0, 1.5, 2.0, 3.0)
    assert LEXICAL_WEIGHT_GRID[0] == 1.0
    assert min(LEXICAL_WEIGHT_GRID) == 1.0


def test_pick_best_lexical_weight_prefers_highest_success() -> None:
    """Primary key: the weight with the highest dev success rate wins."""
    results = [
        _lw(1.0, success_rate=0.50),
        _lw(2.0, success_rate=0.70),
        _lw(3.0, success_rate=0.60),
    ]
    assert pick_best_lexical_weight(results).lexical_weight == 2.0


def test_pick_best_lexical_weight_cost_then_tokens_break_tie() -> None:
    """Equal success -> lowest cost, then lowest mean context tokens."""
    cost_tie = [
        _lw(1.0, success_rate=0.70, cost=2.0),
        _lw(2.0, success_rate=0.70, cost=1.0),
    ]
    assert pick_best_lexical_weight(cost_tie).lexical_weight == 2.0

    token_tie = [
        _lw(1.5, success_rate=0.70, cost=1.0, ctx=200.0),
        _lw(3.0, success_rate=0.70, cost=1.0, ctx=120.0),
    ]
    assert pick_best_lexical_weight(token_tie).lexical_weight == 3.0


def test_pick_best_lexical_weight_final_tie_prefers_identity() -> None:
    """All metrics equal -> lowest weight (the 1.0 identity) wins, order-free."""
    results = [
        _lw(3.0, success_rate=0.70),
        _lw(1.0, success_rate=0.70),
        _lw(2.0, success_rate=0.70),
    ]
    best = pick_best_lexical_weight(results)
    assert best.lexical_weight == 1.0
    assert pick_best_lexical_weight(list(reversed(results))) == best


def test_pick_best_lexical_weight_empty_raises() -> None:
    """An empty result list has no winner to pick."""
    with pytest.raises(ValueError, match="at least one"):
        pick_best_lexical_weight([])
