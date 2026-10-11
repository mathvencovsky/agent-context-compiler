"""End-to-end run over the fixture corpus with the FAKE provider (crit. (a)/(b)).

Runs ``bench.run`` against the committed acceptance fixtures and proves:

* (a) the three output files are generated under ``<results_root>/<timestamp>/``;
* (b) two consecutive runs produce byte-identical ``results.csv`` and
  ``report.md`` once the directory name (timestamp) and the ``latency_ms`` column
  are ignored.

The default providers are the deterministic fakes, so no network is touched.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from bench import run as run_module


def _strip_latency(csv_text: str) -> str:
    """Return ``csv_text`` with the time-dependent ``latency_ms`` column removed."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    header = rows[0]
    idx = header.index("latency_ms")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows:
        writer.writerow([v for i, v in enumerate(row) if i != idx])
    return out.getvalue()


def test_e2e_generates_three_files(
    tmp_path: Path, fixture_corpus_dir: str, fixture_tasks_path: str
) -> None:
    out = run_module.run(
        strategies=["raw_html", "naive_text", "naive_rag"],
        split="dev",
        budget=400,
        corpus_dir=fixture_corpus_dir,
        tasks_path=fixture_tasks_path,
        results_root=tmp_path,
        timestamp="20260101T000000_000000",
    )
    assert (out / "results.csv").is_file()
    assert (out / "report.md").is_file()
    assert (out / "config.json").is_file()


def test_e2e_two_runs_byte_identical_modulo_time(
    tmp_path: Path, fixture_corpus_dir: str, fixture_tasks_path: str
) -> None:
    first = run_module.run(
        strategies=["raw_html", "naive_text", "naive_rag"],
        split="dev",
        budget=400,
        corpus_dir=fixture_corpus_dir,
        tasks_path=fixture_tasks_path,
        results_root=tmp_path,
        timestamp="RUN_A",
    )
    second = run_module.run(
        strategies=["raw_html", "naive_text", "naive_rag"],
        split="dev",
        budget=400,
        corpus_dir=fixture_corpus_dir,
        tasks_path=fixture_tasks_path,
        results_root=tmp_path,
        timestamp="RUN_B",
    )

    # report.md carries no time columns, so it must match byte for byte.
    assert (first / "report.md").read_bytes() == (second / "report.md").read_bytes()

    # results.csv matches once latency_ms (the only time column) is dropped.
    a = _strip_latency((first / "results.csv").read_text(encoding="utf-8"))
    b = _strip_latency((second / "results.csv").read_text(encoding="utf-8"))
    assert a == b


def test_e2e_report_notes_ignored_task(
    tmp_path: Path, fixture_corpus_dir: str, fixture_tasks_path: str
) -> None:
    out = run_module.run(
        strategies=["raw_html"],
        split="dev",
        budget=400,
        corpus_dir=fixture_corpus_dir,
        tasks_path=fixture_tasks_path,
        results_root=tmp_path,
        timestamp="TS",
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    # The fixture has exactly one validated:false task.
    assert "tasks ignored (validated:false): 1" in report
