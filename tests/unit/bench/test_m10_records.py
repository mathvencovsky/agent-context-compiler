"""Tests for the M10 output files (bench/m10_records.py)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from bench.label_answers import draw_sample
from bench.m10_records import SessionRecord, append_answer, append_record, done_pairs
from bench.power import discordance


def test_results_csv_appends_rows_under_one_header(tmp_path: Path) -> None:
    path = tmp_path / "results.csv"
    append_record(
        path,
        SessionRecord(
            "so-1", "nina", "pilot", "python", "code", correct=True, cost_usd=0.0123
        ),
    )
    append_record(path, SessionRecord("so-1", "full_page", "pilot", "python", "code"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("task_id,strategy,split,site,task_type,correct,")
    rows = list(csv.DictReader(lines))
    assert (rows[0]["correct"], rows[0]["cost_usd"]) == ("1", "0.01230000")
    assert rows[1]["correct"] == ""  # excluded session: no verdict
    assert rows[0]["abstained"] == "" and "abstained" in lines[0]  # Amendment 13
    assert done_pairs(path) == {("so-1", "nina"), ("so-1", "full_page")}
    assert done_pairs(tmp_path / "missing.csv") == set()


def test_power_skips_excluded_sessions(tmp_path: Path) -> None:
    path = tmp_path / "results.csv"
    for task, nina, full in (("so-1", True, False), ("so-2", None, True)):
        append_record(path, SessionRecord(task, "nina", "pilot", "x", "fact", nina))
        append_record(
            path, SessionRecord(task, "full_page", "pilot", "x", "fact", full)
        )
    with path.open(encoding="utf-8", newline="") as fh:
        d = discordance(csv.DictReader(fh), "nina", "full_page")
    # so-2 is excluded (no nina verdict), so only so-1 is paired.
    assert (d.n, d.a_only, d.b_only) == (1, 1, 0)


def test_answers_jsonl_feeds_the_blind_labels(tmp_path: Path) -> None:
    path = tmp_path / "answers.jsonl"
    append_answer(path, task_id="so-1", arm="nina", answer="ação", judge_correct=True)
    append_answer(path, task_id="so-2", arm="nina", answer="x", judge_correct=None)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {
        "task_id": "so-1",
        "arm": "nina",
        "answer": "ação",
        "judge_correct": True,
    }
    assert [r["task_id"] for r in draw_sample(rows)] == ["so-1"]
