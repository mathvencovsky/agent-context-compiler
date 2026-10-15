"""Tests for the M10 sample-size calculator (bench/power.py, PREREG §5)."""

from __future__ import annotations

import pytest

from bench.power import achieved_delta, discordance, required_n


def _row(
    task: str, strategy: str, correct: bool, budget: str = "4000"
) -> dict[str, str]:
    return {
        "task_id": task,
        "strategy": strategy,
        "budget": budget,
        "correct": "1" if correct else "0",
    }


def test_required_n_matches_prereg_table() -> None:
    # The PREREG table (one-sided alpha 0.025, power 0.80), rounded up.
    assert required_n(0.25, 0.075) == 349
    assert required_n(0.15, 0.10) == 118
    assert required_n(0.35, 0.05) == 1099


def test_achieved_delta_inverts_required_n() -> None:
    n = required_n(0.25, 0.075)
    assert achieved_delta(0.25, n) <= 0.075
    assert achieved_delta(0.25, n - 1) > 0.075


def test_discordance_pairs_tasks_and_ignores_unpaired() -> None:
    rows = [
        _row("t1", "ours", True),
        _row("t1", "strong_rag", False),  # ours-only
        _row("t2", "ours", False),
        _row("t2", "strong_rag", True),  # strong_rag-only
        _row("t3", "ours", True),
        _row("t3", "strong_rag", True),  # concordant
        _row("t4", "ours", True),  # unpaired: ignored
        _row("t1", "naive_rag", False),  # other strategy: ignored
        _row("t3", "ours", False, budget="2000"),  # other budget: filtered
    ]
    d = discordance(rows, "ours", "strong_rag", budget="4000")
    assert (d.n, d.a_only, d.b_only) == (3, 1, 1)
    assert d.psi == pytest.approx(2 / 3)


def test_discordance_rejects_ambiguous_budgets() -> None:
    rows = [
        _row("t1", "ours", True, budget="2000"),
        _row("t1", "ours", False, budget="4000"),
        _row("t1", "strong_rag", True),
    ]
    with pytest.raises(ValueError, match="pass a budget"):
        discordance(rows, "ours", "strong_rag")
