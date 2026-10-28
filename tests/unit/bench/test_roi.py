"""ROI model of PREREG Amendment 4, with and without context editing (bench/roi.py)."""

from __future__ import annotations

import math

from bench.roi import (
    TaskCost,
    bootstrap_ratio,
    cost_at,
    cpt,
    paired_tasks,
    ratio,
    report,
)

P = 1e-7  # USD per cached token read


def test_cost_grows_with_rereads_until_the_result_is_cleared() -> None:
    t = TaskCost(cost_usd=0.10, tool_tokens=40_000, correct=True)
    assert cost_at(t, 0, o=0.0, p_read=P, clear_after=None) == 0.10
    assert math.isclose(cost_at(t, 10, o=0.0, p_read=P, clear_after=None), 0.14)
    # cleared after 3 turns: only 3 rereads, but the other-work cost keeps going
    assert math.isclose(cost_at(t, 10, o=0.001, p_read=P, clear_after=3), 0.122)


def test_cpt_and_ratio_follow_the_amendment_4_formula() -> None:
    full = [TaskCost(0.10, 40_000, True), TaskCost(0.10, 40_000, False)]
    nina = [TaskCost(0.05, 4_000, True), TaskCost(0.05, 4_000, True)]
    assert cpt(full, 0, o=0.0, p_read=P) == 0.20
    assert cpt(nina, 0, o=0.0, p_read=P) == 0.05
    assert ratio(full, nina, 0, o=0.0, p_read=P) == 4.0
    # long sessions favor the small result; clearing gives part of it back
    long_run = ratio(full, nina, 100, o=0.0, p_read=P)
    cleared = ratio(full, nina, 100, o=0.0, p_read=P, clear_after=3)
    assert long_run > cleared > 4.0
    assert cpt([TaskCost(1.0, 0, False)], 0, o=0.0, p_read=P) == math.inf


def test_bootstrap_interval_contains_the_point_estimate() -> None:
    full = [TaskCost(0.1 + i / 100, 30_000, i % 3 != 0) for i in range(12)]
    nina = [TaskCost(0.05, 3_000, i % 4 != 0) for i in range(12)]
    r, lo, hi = bootstrap_ratio(full, nina, 10, o=0.0, p_read=P, n_boot=500)
    assert lo <= r <= hi


def test_paired_tasks_keeps_judged_primary_tasks_in_both_arms() -> None:
    def row(task: str, arm: str, correct: str, split: str = "holdout") -> dict:
        return {"task_id": task, "strategy": arm, "split": split,
                "correct": correct, "cost_usd": "0.1",
                "tool_result_tokens": "100"}  # fmt: skip

    rows = [
        row("a", "full_page", "1"),
        row("a", "nina", "0"),
        row("b", "full_page", "1"),
        row("b", "nina", ""),  # excluded session
        row("c", "full_page", "1", split="pilot"),
        row("c", "nina", "1", split="pilot"),
    ]
    full, nina = paired_tasks(rows)
    assert full == [TaskCost(0.1, 100, True)]
    assert nina == [TaskCost(0.1, 100, False)]


def test_report_shows_both_scenarios() -> None:
    full = [TaskCost(0.10, 40_000, True)] * 4
    nina = [TaskCost(0.05, 4_000, True)] * 4
    text = report(full, nina, p_read=P, o=0.0, clear_after=3, volume=1000,
                  ks=(0, 10), n_boot=50)  # fmt: skip
    assert "| no clearing | 0 | 2.00 |" in text
    assert "| tool results cleared after 3 turns | 10 |" in text
    assert "a model, not a measurement" in text
