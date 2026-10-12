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
    CombinationResult,
    coverage_from_scores,
    percentiles,
    pick_best,
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
