"""Report aggregation: ignores validated:false and reports the count (crit. (d)).

Also checks the three output files are written, cost pricing from prices.yaml,
and that re-aggregating identical rows produces byte-identical results.csv and
report.md (determinism, ignoring only the directory name).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.report import ResultRow, aggregate, model_cost_usd

_PRICES = {
    "selected": {"answerer": "gpt-6.1-sol", "judge": "claude-sonnet-5-5"},
    "models": {
        "gpt-6.1-sol": {"input_usd_per_1m": 2.0, "output_usd_per_1m": 10.0},
    },
}


def _row(task_id: str, *, validated: bool, correct: bool = True) -> ResultRow:
    return ResultRow(
        task_id=task_id,
        strategy="raw_html",
        category="api_reference",
        split="dev",
        answer_type="judge",
        context_tokens=100,
        input_tokens=1000,
        output_tokens=200,
        correct=correct,
        citation_correct=True,
        is_insufficient=False,
        cost_usd=0.004,
        latency_ms=1.23,
        validated=validated,
    )


def test_ignores_unvalidated_and_reports_count(tmp_path: Path) -> None:
    rows = [
        _row("t1", validated=True),
        _row("t2", validated=True),
        _row("t3", validated=False),
        _row("t4", validated=False),
    ]
    out = aggregate(
        rows,
        prices=_PRICES,
        budget=8000,
        split="dev",
        results_root=tmp_path,
        timestamp="FIXED",
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    csv_text = (out / "results.csv").read_text(encoding="utf-8")

    assert "tasks ignored (validated:false): 2" in report
    # Only the two validated tasks appear in the CSV data rows.
    assert "t1" in csv_text and "t2" in csv_text
    assert "t3" not in csv_text and "t4" not in csv_text


def test_writes_three_files(tmp_path: Path) -> None:
    out = aggregate(
        [_row("t1", validated=True)],
        prices=_PRICES,
        budget=8000,
        split="dev",
        results_root=tmp_path,
        timestamp="TS",
    )
    assert (out / "results.csv").is_file()
    assert (out / "report.md").is_file()
    assert (out / "config.json").is_file()


def test_cost_pricing() -> None:
    cost = model_cost_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="gpt-6.1-sol",
        prices=_PRICES,
    )
    assert cost == pytest.approx(12.0)


def test_unknown_model_costs_zero() -> None:
    assert (
        model_cost_usd(
            input_tokens=1000, output_tokens=1000, model="nope", prices=_PRICES
        )
        == 0.0
    )


def test_reaggregation_is_byte_stable(tmp_path: Path) -> None:
    rows = [_row("t1", validated=True), _row("t2", validated=True, correct=False)]
    first = aggregate(
        rows,
        prices=_PRICES,
        budget=8000,
        split="dev",
        results_root=tmp_path,
        timestamp="A",
    )
    second = aggregate(
        rows,
        prices=_PRICES,
        budget=8000,
        split="dev",
        results_root=tmp_path,
        timestamp="B",
    )
    assert (first / "results.csv").read_bytes() == (second / "results.csv").read_bytes()
    assert (first / "report.md").read_bytes() == (second / "report.md").read_bytes()
