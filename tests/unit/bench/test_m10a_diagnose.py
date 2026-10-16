"""Offline tests for the M10a failure diagnosis (bench/m10a_diagnose.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.m10a_baseline import BUDGETS
from bench.m10a_diagnose import cause, paired, trace_task
from tests.unit.bench.test_m10a_baseline import _ANSWER, _store, _task


def test_cause_takes_the_first_stage_that_lost_the_section() -> None:
    base = {"ir": 1.0, "best_chunk": 1.0, "pool": 1.0}
    assert cause(base | {"ir": 0.2}) == "extraction_loss"
    assert cause(base | {"pool": 0.0, "best_chunk": 0.3}) == "out_of_pool_split"
    assert cause(base | {"pool": 0.0}) == "out_of_pool_ranked"
    assert cause(base) == "selection_drop"


def test_paired_difference_interval_and_mcnemar() -> None:
    a = [True] * 6 + [False] * 4
    b = [True] * 2 + [False] * 8
    s = paired(a, b)
    assert s["diff"] == pytest.approx(0.4)
    assert (s["only_a"], s["only_b"]) == (4, 0)
    assert s["lo"] <= 0.4 <= s["hi"]
    assert s["p"] == pytest.approx(0.125)  # two-sided exact binomial, 4 vs 0
    assert paired([True], [True])["p"] == 1.0


def test_trace_finds_the_section_at_every_stage(tmp_path: Path) -> None:
    key = {
        "url": "https://d.test/select",
        "section": f"LIMIT Clause\n{_ANSWER}",
        "section_tokens": 15,
    }
    trace = trace_task(_task(), key, _store(tmp_path))
    assert trace["ir"] == trace["index"] == trace["best_chunk"] == 1.0
    assert trace["pool_n"] > 0 and trace["pool_tokens"] > 0
    assert set(trace) >= {f"ctx_{b}" for b in BUDGETS}
