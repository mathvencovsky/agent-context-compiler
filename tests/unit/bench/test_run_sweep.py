"""Offline tests for the M8 Stage 2 budget sweep + extended report (CLAUDE.md).

Everything here runs with the deterministic FAKE providers over the committed
fixture corpus — zero network, zero keys (the autouse ``_no_network`` guard in
``conftest.py`` fails any accidental connection). The tests cover:

* the retrieval-injection seam (an instrumented fake reranker wired into the
  ``ours*`` runners is actually called);
* the budget sweep produces ``results.csv`` with a ``budget`` column holding the
  three swept budgets, and is byte-identical across runs modulo ``latency_ms``;
* the deterministic failure classifier returns exactly one of the four labels
  for synthetic probes exercising each branch;
* ``config.json`` records the five frozen params and ``budgets``.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app.providers.llm.fake import FakeLLMProvider
from bench import report_m8, sweep
from bench.corpus_store import CorpusStore
from bench.report_m8 import diagnose_failure
from bench.runners.base import _REGISTRY, register_ours
from bench.sweep import FailureProbe, build_sweep_rows
from bench.tasks_schema import load_tasks

_FIXTURE_BENCH = Path(__file__).resolve().parents[2] / "fixtures" / "bench"
_CORPUS_DIR = str(_FIXTURE_BENCH / "corpus")
_TASKS_PATH = str(_FIXTURE_BENCH / "tasks.yaml")

_STRATEGIES = [
    "raw_html",
    "naive_rag",
    "naive_rag_vector",
    "ours",
    "ours_no_rerank",
]
_BUDGETS = [400, 800, 1600]


class _CountingReranker:
    """Offline reranker that records calls and forces one chunk to the top."""

    model = "counting-reranker"

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        return [1.0 if i == 0 else 0.0 for i, _ in enumerate(documents)]


class _CountingEmbeddings:
    """Offline embeddings delegate wrapping the deterministic fake, with a counter."""

    def __init__(self) -> None:
        from app.providers.embeddings.fake import FakeEmbeddingProvider

        self._fake = FakeEmbeddingProvider()
        self.model = self._fake.model
        self.dim = self._fake.dim
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return self._fake.embed(texts)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    """Reset the runner registry around each test so injections do not leak."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def _fake_providers() -> tuple[FakeLLMProvider, FakeLLMProvider]:
    return FakeLLMProvider(model="fake-answerer"), FakeLLMProvider(model="fake-judge")


def test_injected_retrieval_is_used() -> None:
    """A reranker/embeddings pair injected via ``register_ours`` is really used."""
    reranker = _CountingReranker()
    embeddings = _CountingEmbeddings()
    register_ours(embeddings=embeddings, reranker=reranker, lambda_=0.3, min_value=0.0)

    tasks = load_tasks(_TASKS_PATH)
    store = CorpusStore(_CORPUS_DIR)
    answerer, judge = _fake_providers()
    rows, probes = build_sweep_rows(
        tasks=tasks,
        strategies=["ours"],
        budgets=[800],
        store=store,
        answerer=answerer,
        judge=judge,
        prices={},
        reference_budget=800,
    )
    assert reranker.calls > 0, "injected reranker was never called"
    assert embeddings.calls > 0, "injected embeddings were never called"
    assert rows
    assert all(s.budget == 800 for s in rows)
    # One probe per ours task at the reference budget.
    assert len(probes) == len(tasks)


def test_sweep_writes_results_csv_with_budget_column(tmp_path: Path) -> None:
    """``results.csv`` has a ``budget`` column holding the three swept budgets."""
    out = sweep.run_sweep(
        strategies=_STRATEGIES,
        split="dev",
        budgets=_BUDGETS,
        corpus_dir=_CORPUS_DIR,
        tasks_path=_TASKS_PATH,
        results_root=tmp_path,
        prices_path=_FIXTURE_BENCH.parent.parent.parent / "bench" / "prices.yaml",
        out_name="run_a",
        provider_factory=_fake_providers,
        retrieval_factory=lambda: (_CountingEmbeddings(), _CountingReranker()),
    )
    assert (out / "results.csv").is_file()
    assert (out / "report.md").is_file()
    assert (out / "config.json").is_file()
    assert (out / "sumario.md").is_file()

    text = (out / "results.csv").read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    assert "budget" in header
    budget_idx = header.index("budget")
    budgets = {int(row[budget_idx]) for row in reader}
    assert budgets == set(_BUDGETS)


def _strip_report_time(report_text: str) -> str:
    """Drop time-dependent fields from report.md for the determinism check.

    Stage 2's report.md includes latency p50/p95 (per SPEC §10.5 / M8) and the
    retrieval vendor-call counters — both inherently per-run, like the CSV's
    ``latency_ms``. They are stripped here so the comparison exercises only the
    byte-stable measured metrics (success, CIs, tokens, cost, rates).
    """
    out: list[str] = []
    for line in report_text.splitlines():
        if line.startswith(("- rerank ", "- embeddings ")):
            continue
        # Table rows end with "... | lat_p50 | lat_p95 |"; drop the last two cols.
        if line.startswith("| ") and line.count("|") >= 12:
            cells = line.split("|")
            line = "|".join(cells[:-3]) + "|"
        out.append(line)
    return "\n".join(out)


def _strip_latency(csv_text: str) -> str:
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    idx = rows[0].index("latency_ms")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows:
        writer.writerow([v for i, v in enumerate(row) if i != idx])
    return out.getvalue()


def test_sweep_is_deterministic_modulo_time(tmp_path: Path) -> None:
    """Two sweeps produce byte-identical report.md and results.csv (sans latency)."""

    def _run(name: str) -> Path:
        _REGISTRY.clear()
        return sweep.run_sweep(
            strategies=_STRATEGIES,
            split="dev",
            budgets=_BUDGETS,
            corpus_dir=_CORPUS_DIR,
            tasks_path=_TASKS_PATH,
            results_root=tmp_path,
            prices_path=_FIXTURE_BENCH.parent.parent.parent / "bench" / "prices.yaml",
            out_name=name,
            provider_factory=_fake_providers,
            retrieval_factory=lambda: (_CountingEmbeddings(), _CountingReranker()),
        )

    first = _run("run_a")
    second = _run("run_b")
    # The retrieval vendor-call counters reflect per-run cache/provider state
    # (like latency), so they are excluded from the determinism comparison; the
    # measured metrics in the rest of the report are byte-stable.
    assert _strip_report_time(
        (first / "report.md").read_text(encoding="utf-8")
    ) == _strip_report_time((second / "report.md").read_text(encoding="utf-8"))
    assert (first / "sumario.md").read_bytes() == (second / "sumario.md").read_bytes()
    a = _strip_latency((first / "results.csv").read_text(encoding="utf-8"))
    b = _strip_latency((second / "results.csv").read_text(encoding="utf-8"))
    assert a == b


def test_config_json_records_frozen_params_and_budgets(tmp_path: Path) -> None:
    """``config.json`` carries the frozen params (incl. lexical_weight) + budgets."""
    out = sweep.run_sweep(
        strategies=["ours", "naive_rag"],
        split="dev",
        budgets=_BUDGETS,
        corpus_dir=_CORPUS_DIR,
        tasks_path=_TASKS_PATH,
        results_root=tmp_path,
        prices_path=_FIXTURE_BENCH.parent.parent.parent / "bench" / "prices.yaml",
        out_name="cfg",
        provider_factory=_fake_providers,
        retrieval_factory=lambda: (_CountingEmbeddings(), _CountingReranker()),
    )
    config = json.loads((out / "config.json").read_text(encoding="utf-8"))
    frozen = config["frozen_params"]
    assert set(frozen) == {
        "selection_lambda",
        "min_value",
        "coverage_low",
        "coverage_high",
        "rrf_k",
        "lexical_weight",
    }
    # The new M8 knob (FEAT-001) is recorded for reproducibility; default 1.0.
    assert "lexical_weight" in frozen
    assert frozen["lexical_weight"] == 1.0
    assert config["budgets"] == _BUDGETS


def test_sweep_compares_ours_vs_both_baselines(tmp_path: Path) -> None:
    """report.md + sumario.md carry the ours-vs-naive_rag_vector comparison too."""
    out = sweep.run_sweep(
        strategies=_STRATEGIES,
        split="dev",
        budgets=_BUDGETS,
        corpus_dir=_CORPUS_DIR,
        tasks_path=_TASKS_PATH,
        results_root=tmp_path,
        prices_path=_FIXTURE_BENCH.parent.parent.parent / "bench" / "prices.yaml",
        out_name="cmp",
        provider_factory=_fake_providers,
        retrieval_factory=lambda: (_CountingEmbeddings(), _CountingReranker()),
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    sumario = (out / "sumario.md").read_text(encoding="utf-8")
    # Both baseline comparison blocks are present in the report.
    assert "ours vs naive_rag by category (reference budget)" in report
    assert "ours vs naive_rag_vector by category (reference budget)" in report
    # The sumario covers both baselines and the extended central question.
    assert "`ours` vs `naive_rag`" in sumario
    assert "`ours` vs `naive_rag_vector`" in sumario
    assert "naive_rag` E `naive_rag_vector`" in sumario
    # naive_rag_vector appears in the per-strategy table (iterates all strategies).
    assert "| naive_rag_vector |" in report


def test_report_notes_ignored_unvalidated_task(tmp_path: Path) -> None:
    """The one validated:false fixture task is reported as ignored."""
    out = sweep.run_sweep(
        strategies=["naive_rag"],
        split="dev",
        budgets=[800],
        corpus_dir=_CORPUS_DIR,
        tasks_path=_TASKS_PATH,
        results_root=tmp_path,
        prices_path=_FIXTURE_BENCH.parent.parent.parent / "bench" / "prices.yaml",
        out_name="ign",
        provider_factory=_fake_providers,
        retrieval_factory=lambda: (_CountingEmbeddings(), _CountingReranker()),
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "tasks ignored (validated:false): 1" in report


def _probe(**kw: object) -> FailureProbe:
    base: dict[str, object] = {
        "task_id": "t",
        "category": "guide",
        "is_insufficient": False,
        "correct": False,
        "citation_correct": False,
        "evidence_in_candidates": False,
        "evidence_in_context": False,
    }
    base.update(kw)
    return FailureProbe(**base)  # type: ignore[arg-type]


def test_diagnose_failure_covers_each_branch() -> None:
    """The classifier returns one of the three failure labels per branch (DS6).

    A failure is a ``correct=False`` task (SPEC §10.4). Citation precision is a
    separate metric, so a correct answer is never a failure and must not be
    classified.
    """
    # retrieval: evidence never retrieved.
    assert diagnose_failure(_probe(evidence_in_candidates=False)) == "retrieval"
    # selection: retrieved but not in the emitted context.
    assert (
        diagnose_failure(_probe(evidence_in_candidates=True, evidence_in_context=False))
        == "selection"
    )
    # answer: in context but wrong/INSUFFICIENT.
    assert (
        diagnose_failure(
            _probe(
                evidence_in_candidates=True,
                evidence_in_context=True,
                is_insufficient=True,
            )
        )
        == "answer"
    )


def test_diagnose_failure_rejects_a_correct_probe() -> None:
    """A ``correct=True`` probe is not a failure and must not be diagnosed."""
    with pytest.raises(ValueError, match="correct"):
        diagnose_failure(_probe(correct=True, citation_correct=False))


def test_failures_section_excludes_correct_but_uncited() -> None:
    """Correct-but-uncited probes are successes, not failures (SPEC §10.4).

    This is the review fix: the table must be built from ``correct=False`` rows
    only, never from ``not (correct and cited)``.
    """
    probes = [
        _probe(task_id="t00", correct=True, citation_correct=False),  # success
        _probe(task_id="t01", correct=True, citation_correct=True),  # success
        _probe(task_id="t02", correct=False, evidence_in_candidates=False),  # failure
    ]
    body = "\n".join(report_m8._failures_section(probes))
    assert "| t02 |" in body
    assert "| t00 |" not in body
    assert "| t01 |" not in body
    assert "total ours failures at budget 4000: 1" in body


def test_citation_signal_section_reports_uncited_answers() -> None:
    """The citation-signal section surfaces that ours emits no verified cite."""
    probes = [
        _probe(task_id="t00", correct=True, citation_correct=False),
        _probe(task_id="t01", correct=False, citation_correct=False),
    ]
    body = "\n".join(report_m8._citation_signal_section(probes))
    assert "citation signal" in body
    assert "tasks graded: 2" in body
    assert "tasks correct (judge `correct=1`): 1" in body
    assert "VERIFIED citation: 0" in body


def test_report_lists_at_most_ten_failures() -> None:
    """The failures section lists at most ten probes, ordered by task_id."""
    probes = [_probe(task_id=f"t{i:02d}") for i in range(15)]
    section = report_m8._failures_section(probes)
    body = "\n".join(section)
    # Row numbers 1..10 appear; 11+ do not get their own numbered row.
    assert "| 10 | t09 |" in body
    assert "| 11 |" not in body
    assert "total ours failures at budget 4000: 15" in body
