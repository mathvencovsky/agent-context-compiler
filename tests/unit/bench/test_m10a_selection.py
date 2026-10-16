"""Tests for the M10a selection experiment (bench/m10a_selection.py)."""

from __future__ import annotations

import math

from app.retrieve.select import Candidate, select
from bench.m10a_selection import select_variant


def _c(ord_: int, value: float, vec: list[float], tokens: int = 100) -> Candidate:
    return Candidate(
        chunk_id=0, document_id="d", ord=ord_, value=value, tokens=tokens, embedding=vec
    )


_POOL = [
    _c(0, 1.0, [1.0, 0.0]),
    _c(1, 0.2, [1.0, 0.0]),  # low value and a near-duplicate of 0
    _c(2, 0.9, [0.0, 1.0]),
]


def test_without_a_gate_it_matches_the_app_selector_and_says_why_it_stopped() -> None:
    for budget in (100, 250, 1_000):
        reasons: list[str] = []
        got = select_variant(
            _POOL, budget_tokens=budget, lambda_=0.3, min_value=0.0, reasons=reasons
        )
        assert got == select(_POOL, budget_tokens=budget, lambda_=0.3, min_value=0.0)
    # 0 and 2 fit; 1 now scores (0.2 - 0.3) < 0 and ends the selection.
    assert reasons == ["adjusted_value_below_min"]


def test_a_gate_lets_the_penalty_order_but_not_stop() -> None:
    reasons: list[str] = []
    got = select_variant(
        _POOL,
        budget_tokens=1_000,
        lambda_=0.3,
        min_value=-math.inf,
        gate=0.1,
        reasons=reasons,
    )
    assert [c.ord for c in got] == [0, 1, 2]
    assert reasons == ["pool_exhausted"]
    reasons.clear()
    got = select_variant(
        _POOL, budget_tokens=1_000, lambda_=0.3, min_value=-math.inf, gate=0.5,
        reasons=reasons,
    )  # fmt: skip
    assert [c.ord for c in got] == [0, 2] and reasons == ["gate"]
