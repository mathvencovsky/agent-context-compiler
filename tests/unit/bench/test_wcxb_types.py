"""Per-type summary and acceptance rule of bench/extract_eval/wcxb_types.py."""

from __future__ import annotations

from typing import Any

from bench.extract_eval.wcxb_types import NON_DOC, summarize, verdict


def _cell(f1: float = 0.9, without: float = 0.2, recall: float = 0.9) -> dict[str, Any]:
    return {
        "f1": f1,
        "recall": recall,
        "precision": 0.9,
        "without": without,
        "leak": 0.1,
        "code_text_found": 9,
        "code_total": 10,
        "cell_found": 9,
        "cell_total": 10,
    }


def _rows(without: float = 0.2, f1: float = 0.9, recall: float = 0.9) -> list[dict]:
    rows = []
    for kind in (*NON_DOC, "documentation"):
        cell = _cell(f1, without, recall)
        rows.append(
            {"type": kind, "nina": cell, "trafilatura": _cell(), "readability": _cell()}
        )
    return rows


def test_summary_has_each_type_and_all() -> None:
    summary = summarize(_rows())
    assert summary["all"]["pages"] == 7
    assert summary["product"]["pages"] == 1
    assert summary["product"]["nina"]["code"] == 0.9


def test_verdict_passes_when_boilerplate_falls_and_f1_rises() -> None:
    rows = verdict(summarize(_rows()), summarize(_rows(without=0.1, f1=0.91)))
    assert all(passed for _, passed, _ in rows)


def test_verdict_fails_when_docs_recall_or_f1_drop() -> None:
    before = summarize(_rows())
    failed = {
        check
        for check, passed, _ in verdict(
            before, summarize(_rows(without=0.1, f1=0.85, recall=0.8))
        )
        if not passed
    }
    assert "2. f1 falls <= 1 pt in any type" in failed
    assert "3. docs recall <= 0.5 pt" in failed
