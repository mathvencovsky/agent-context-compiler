"""Tests for the A1 adaptive-budget experiment (bench/m10a_adaptive.py)."""

from __future__ import annotations

from typing import Any

from app.retrieve.select import Candidate, select
from bench.m10a_adaptive import accept, select_adaptive


def _c(ord_: int, value: float) -> Candidate:
    return Candidate(chunk_id=0, document_id="d", ord=ord_, value=value, tokens=100,
                     embedding=[1.0, float(ord_)])  # fmt: skip


_POOL = [_c(0, 0.9), _c(1, 0.8), _c(2, 0.3), _c(3, 0.05)]


def _ords(kind: str, param: float) -> list[int]:
    got = select_adaptive(_POOL, budget_tokens=1_000, lambda_=0.0, min_value=0.0,
                          kind=kind, param=param)  # fmt: skip
    return [c.ord for c in got]


def test_none_is_the_app_selector() -> None:
    assert select_adaptive(_POOL, budget_tokens=250, lambda_=0.0, min_value=0.0,
                           kind="none", param=0.0) == select(
        _POOL, budget_tokens=250, lambda_=0.0, min_value=0.0)  # fmt: skip


def test_cuts_drop_low_relevance_candidates() -> None:
    assert _ords("abs", 0.1) == [0, 1, 2]
    assert _ords("abs", 0.5) == [0, 1]
    assert _ords("rel", 0.3) == [0, 1, 2]  # 0.3 >= 0.27
    assert _ords("rel", 0.5) == [0, 1]
    assert _ords("elbow", 0.5) == [0, 1]  # 0.3 < 0.5 x 0.8 stops


def _cell(cov: float, tokens: int) -> dict[str, list[Any]]:
    return {"hit": [cov >= 0.5] * 10, "coverage": [cov] * 10, "tokens": [tokens] * 10}


def test_accept_applies_tolerances_and_token_rule() -> None:
    current = {b: _cell(0.6, b) for b in (1000, 4000, 8000)}
    strong = {b: _cell(0.1, b) for b in (1000, 4000, 8000)}
    good = {b: _cell(0.597, int(b * 0.9)) for b in (1000, 4000, 8000)}
    assert all(ok for _, ok, _ in accept(good, current, strong))
    lossy = {b: _cell(0.59, int(b * 0.9)) for b in (1000, 4000, 8000)}
    assert not all(ok for _, ok, _ in accept(lossy, current, strong))
    same_tokens = {b: _cell(0.6, b) for b in (1000, 4000, 8000)}
    assert not all(ok for _, ok, _ in accept(same_tokens, current, strong))
    assert all(ok for _, ok, _ in accept(same_tokens, current, strong, half_a=True))
