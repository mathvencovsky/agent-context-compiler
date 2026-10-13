"""M8 Stage 2 — budget sweep over the test split with retrieval reuse (SPEC §10.5).

The Stage 1 CLI (:mod:`bench.run`) measures ONE budget / ONE split. Stage 2 must
measure the SAME strategies across THREE budgets (2000/4000/8000) on the ``test``
split with the REAL providers, and it must not re-call the paid Jina reranker /
OpenAI embeddings three times for data that does not depend on the budget.

:func:`run_sweep` is the thin driver that satisfies both:

* for the ``ours*`` runners it computes the selection-INVARIANT pipeline
  (``prepare_candidates`` — extract/chunk/embed/fuse/rerank) ONCE per
  ``(task, runner)`` and then applies the pure per-budget selection
  (``assemble_from_candidates``) for each of the three budgets — so the Jina /
  OpenAI retrieval spend is paid once per distinct retrieval config, reused
  across budgets (and served from the persistent ``bench/.cache/`` across
  ablations), exactly as the Stage 1 calibrator does (DS3);
* for the baselines (``raw_html``/``naive_text``/``naive_rag``) it builds the
  context per budget (cheap, offline, no vendor call).

Every ``(task, strategy, budget)`` is answered by the answerer at temperature 0
and graded by the judge, priced from ``bench/prices.yaml`` — the SAME measurement
path as :func:`bench.run._grade_one`. The result is one :class:`SweepRow` per
``(task, strategy, budget)``; :func:`write_results_csv` emits the combined
``results.csv`` with an extra ``budget`` column.

Determinism / offline posture mirrors the rest of the bench: by default the
providers are the deterministic fakes (tests), so the pure row-building logic
(:func:`build_sweep_rows`) is unit-tested without the network. The REAL providers
(OpenAI answerer, Anthropic judge, OpenAI embeddings, Jina reranker) are selected
only on the human ``--real`` run and are never used by tests.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLMProvider
from bench.answer import answer
from bench.corpus_store import CorpusStore
from bench.grade import grade
from bench.report import ResultRow, model_cost_usd
from bench.run import (
    DEFAULT_CORPUS_DIR,
    DEFAULT_PRICES_PATH,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_TASKS_PATH,
    load_prices,
)
from bench.runners.base import get_runner
from bench.runners.ours import OursRunner
from bench.tasks_schema import Task, load_tasks

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider

#: The three context budgets swept in Stage 2 (SPEC §10.5 / M8).
DEFAULT_BUDGETS: tuple[int, ...] = (2000, 4000, 8000)

#: Default output directory name under ``bench/results/`` for the Stage 2 run.
DEFAULT_OUT_NAME = "m8-stage2"

#: ``ours*`` strategy names share the selection-invariant prepare step, so their
#: retrieval is computed once per task and reused across budgets (DS3).
_OURS_PREFIX = "ours"

#: Reference budget for the ablation analysis and the failure list (DS7) — the
#: same budget the Stage 1 calibration used, so apples are compared to apples.
REFERENCE_BUDGET = 4000

#: The full ``ours`` strategy whose failures are diagnosed (DS6).
_OURS_FULL = "ours"

#: Combined results.csv columns: the Stage 1 schema PLUS a ``budget`` column so
#: one file holds every (task, strategy, budget) row (DS2).
CSV_COLUMNS: tuple[str, ...] = (
    "task_id",
    "strategy",
    "budget",
    "category",
    "split",
    "answer_type",
    "context_tokens",
    "input_tokens",
    "output_tokens",
    "correct",
    "citation_correct",
    "is_insufficient",
    "cost_usd",
    "latency_ms",
)


@dataclass(frozen=True)
class SweepRow:
    """One graded ``(task, strategy, budget)`` outcome.

    Wraps a Stage 1 :class:`~bench.report.ResultRow` with the ``budget`` the row
    was measured at, so the combined ``results.csv`` and the extended M8 report
    (:mod:`bench.report_m8`) can group by budget.
    """

    budget: int
    row: ResultRow


@dataclass(frozen=True)
class FailureProbe:
    """Per-task evidence for the deterministic ``ours`` failure diagnosis (DS6).

    Captured for the full ``ours`` runner at :data:`REFERENCE_BUDGET`. Holds the
    minimal signals the classifier (:func:`bench.report_m8.diagnose_failure`)
    needs — WITHOUT any LLM: whether an expected-evidence URL reached the
    prepared candidates (retrieval), whether it survived into the emitted context
    (selection), and the grade outcome (answer vs citation). All fields are
    derived locally from the frozen corpus and the task's declared evidence.
    """

    task_id: str
    category: str
    #: True when the answerer declined (INSUFFICIENT) on an answerable task.
    is_insufficient: bool
    #: True when the overall grade was correct (then it is not a failure).
    correct: bool
    #: True when at least one citation was verified against the evidence.
    citation_correct: bool
    #: True when an expected-evidence doc produced any prepared candidate.
    evidence_in_candidates: bool
    #: True when an expected-evidence doc contributed to the emitted context.
    evidence_in_context: bool


def _evidence_document_ids(task: Task) -> set[str]:
    """Return the deterministic document ids of the task's expected-evidence URLs."""
    from app.extract.ir import make_document_id

    return {make_document_id(ev.url) for ev in task.expected_evidence}


def _is_ours(strategy: str) -> bool:
    """Whether ``strategy`` is an ``ours*`` runner (shares the prepare step)."""
    return strategy == _OURS_PREFIX or strategy.startswith(_OURS_PREFIX + "_")


def _grade_context(
    task: Task,
    context_text: str,
    *,
    store: CorpusStore,
    answerer: LLMProvider,
    judge: LLMProvider,
) -> tuple[Any, Any]:
    """Answer + grade one context, returning ``(answer_result, grade_result)``."""
    answer_result = answer(task, context_text, provider=answerer)
    grade_result = grade(task, answer_result, store=store, judge_provider=judge)
    return answer_result, grade_result


def _row_from_graded(
    task: Task,
    strategy: str,
    *,
    budget: int,
    context_tokens: int,
    latency_ms: float,
    answer_result: Any,
    grade_result: Any,
    answerer_model: str,
    prices: dict[str, Any],
) -> ResultRow:
    """Assemble a :class:`ResultRow` from an already-graded answer."""
    cost = model_cost_usd(
        input_tokens=answer_result.input_tokens,
        output_tokens=answer_result.output_tokens,
        model=answerer_model,
        prices=prices,
    )
    return ResultRow(
        task_id=task.id,
        strategy=strategy,
        category=task.category,
        split=task.split,
        answer_type=task.answer_type,
        context_tokens=context_tokens,
        input_tokens=answer_result.input_tokens,
        output_tokens=answer_result.output_tokens,
        correct=grade_result.correct,
        citation_correct=grade_result.citation_correct,
        is_insufficient=grade_result.is_insufficient,
        cost_usd=cost,
        latency_ms=latency_ms,
        validated=task.validated,
    )


def build_sweep_rows(
    *,
    tasks: Sequence[Task],
    strategies: Sequence[str],
    budgets: Sequence[int],
    store: CorpusStore,
    answerer: LLMProvider,
    judge: LLMProvider,
    prices: dict[str, Any],
    reference_budget: int = REFERENCE_BUDGET,
) -> tuple[list[SweepRow], list[FailureProbe]]:
    """Build one :class:`SweepRow` per ``(task, strategy, budget)`` (pure of I/O).

    For an ``ours*`` strategy the selection-invariant pipeline is prepared ONCE
    per task (``prepare_candidates``) and only the pure per-budget selection is
    re-applied (``assemble_from_candidates``) — so the real reranker/embeddings
    are called at most once per ``(task, strategy)`` regardless of how many
    budgets are swept (DS3). Baselines build their context per budget (cheap,
    offline). Timing wraps the context build + answer + grade so ``latency_ms``
    reflects the measured work; it is excluded from the determinism comparison.

    Returns ``(rows, probes)`` where ``probes`` holds a :class:`FailureProbe` for
    EVERY ``ours`` task at ``reference_budget`` (used to diagnose the failures,
    DS6). No file I/O here, so the whole function is unit-testable with the fakes
    (zero network). The caller supplies the providers and the loaded tasks.
    """
    import time

    rows: list[SweepRow] = []
    probes: list[FailureProbe] = []
    for strategy in strategies:
        runner = get_runner(strategy)
        if _is_ours(strategy):
            if not isinstance(runner, OursRunner):  # pragma: no cover - defensive
                raise TypeError(f"{strategy!r} is not an OursRunner")
            evidence_full = strategy == _OURS_FULL
            for task in tasks:
                prepare_start = time.perf_counter()
                prepared = runner.prepare_candidates(task, store=store)
                prepare_ms = (time.perf_counter() - prepare_start) * 1000.0
                candidate_docs = {c.document_id for c in prepared.candidates}
                for budget in budgets:
                    select_start = time.perf_counter()
                    context = runner.assemble_from_candidates(
                        prepared, budget_tokens=budget
                    )
                    latency_ms = (
                        prepare_ms + (time.perf_counter() - select_start) * 1000.0
                    )
                    answer_result, grade_result = _grade_context(
                        task,
                        context.text,
                        store=store,
                        answerer=answerer,
                        judge=judge,
                    )
                    row = _row_from_graded(
                        task,
                        strategy,
                        budget=budget,
                        context_tokens=context.context_tokens,
                        latency_ms=latency_ms,
                        answer_result=answer_result,
                        grade_result=grade_result,
                        answerer_model=answerer.model,
                        prices=prices,
                    )
                    rows.append(SweepRow(budget=budget, row=row))
                    # Capture the diagnosis probe for the full ``ours`` at the
                    # reference budget — the exact rows the failure list uses.
                    if evidence_full and budget == reference_budget:
                        evidence_ids = _evidence_document_ids(task)
                        probes.append(
                            FailureProbe(
                                task_id=task.id,
                                category=task.category,
                                is_insufficient=grade_result.is_insufficient,
                                correct=grade_result.correct,
                                citation_correct=grade_result.citation_correct,
                                evidence_in_candidates=bool(
                                    evidence_ids & candidate_docs
                                ),
                                evidence_in_context=bool(
                                    evidence_ids
                                    & _context_document_ids(context.used_urls)
                                ),
                            )
                        )
        else:
            for task in tasks:
                for budget in budgets:
                    start = time.perf_counter()
                    context = runner.build_context(
                        task, budget_tokens=budget, store=store
                    )
                    latency_ms = (time.perf_counter() - start) * 1000.0
                    answer_result, grade_result = _grade_context(
                        task,
                        context.text,
                        store=store,
                        answerer=answerer,
                        judge=judge,
                    )
                    row = _row_from_graded(
                        task,
                        strategy,
                        budget=budget,
                        context_tokens=context.context_tokens,
                        latency_ms=latency_ms,
                        answer_result=answer_result,
                        grade_result=grade_result,
                        answerer_model=answerer.model,
                        prices=prices,
                    )
                    rows.append(SweepRow(budget=budget, row=row))
    return rows, probes


def _context_document_ids(used_urls: Sequence[str]) -> set[str]:
    """Return the deterministic document ids of the URLs that fed the context."""
    from app.extract.ir import make_document_id

    return {make_document_id(url) for url in used_urls}


def write_results_csv(path: Path, sweep_rows: Sequence[SweepRow]) -> None:
    """Write the combined ``results.csv`` (one line per task x strategy x budget).

    Sorted by ``(strategy, budget, task_id)`` so the file is byte-stable across
    runs; ``latency_ms`` is written but excluded from the determinism comparison
    by the test (same convention as Stage 1).
    """
    import csv
    import io

    ordered = sorted(
        sweep_rows, key=lambda s: (s.row.strategy, s.budget, s.row.task_id)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for s in ordered:
        r = s.row
        writer.writerow(
            [
                r.task_id,
                r.strategy,
                s.budget,
                r.category,
                r.split,
                r.answer_type,
                r.context_tokens,
                r.input_tokens,
                r.output_tokens,
                int(r.correct),
                int(r.citation_correct),
                int(r.is_insufficient),
                f"{r.cost_usd:.8f}",
                f"{r.latency_ms:.3f}",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _make_providers(*, real: bool) -> tuple[LLMProvider, LLMProvider]:
    """Return ``(answerer, judge)`` — the fakes by default, real on ``--real``."""
    if not real:
        return FakeLLMProvider(model="fake-answerer"), FakeLLMProvider(
            model="fake-judge"
        )
    from app.providers.llm.anthropic import AnthropicProvider  # pragma: no cover
    from app.providers.llm.openai import OpenAIProvider  # pragma: no cover

    return OpenAIProvider(), AnthropicProvider()  # pragma: no cover


def _make_retrieval_providers() -> tuple[EmbeddingProvider, RerankProvider]:
    """Construct the REAL embeddings (OpenAI) + reranker (Jina), DISK-CACHED.

    Identical to :func:`bench.calibrate._make_retrieval_providers`: both are
    wrapped in the persistent, content-addressed cache (``bench/.cache/``) so a
    distinct ``(query, documents)`` rerank / text embedding is computed at most
    once — across budgets, ablations and re-runs, and reusing Stage 1's cache.
    Human-only (lazy import so a test never needs the SDK/key).
    """
    from app.providers.embeddings.openai import (
        OpenAIEmbeddingProvider,
    )  # pragma: no cover
    from app.providers.rerank.jina import JinaRerankProvider  # pragma: no cover
    from bench.provider_cache import (  # pragma: no cover
        CachingEmbeddingProvider,
        CachingRerankProvider,
    )

    return (  # pragma: no cover
        CachingEmbeddingProvider(OpenAIEmbeddingProvider()),
        CachingRerankProvider(JinaRerankProvider()),
    )


def run_sweep(
    *,
    strategies: Sequence[str],
    split: str = "test",
    budgets: Sequence[int] = DEFAULT_BUDGETS,
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    tasks_path: str | Path = DEFAULT_TASKS_PATH,
    results_root: str | Path = DEFAULT_RESULTS_ROOT,
    prices_path: str | Path = DEFAULT_PRICES_PATH,
    out_name: str = DEFAULT_OUT_NAME,
    real: bool = False,
    provider_factory: Callable[[], tuple[LLMProvider, LLMProvider]] | None = None,
    retrieval_factory: (
        Callable[[], tuple[EmbeddingProvider, RerankProvider]] | None
    ) = None,
) -> Path:
    """Run the budget sweep on ``split`` and write the Stage 2 artifacts.

    Loads the ``split`` tasks ONCE, wires the retrieval providers into the
    ``ours*`` runners (the real disk-cached pair on ``--real``; the fakes by
    default) together with the FROZEN selection knobs read from
    ``app.config.get_settings()`` (λ/min_value — only READ, never written), then
    measures every ``(task, strategy, budget)`` via :func:`build_sweep_rows`.
    Writes ``<results_root>/<out_name>/{results.csv, report.md, config.json,
    sumario.md ...}`` through :mod:`bench.report_m8`. Returns the output dir.

    The ``*_factory`` seams let a test drive the whole path with instrumented
    fakes (no network); the pytest suite exercises them, the ``--real`` default
    is the only network path and is never collected.
    """
    from bench import report_m8
    from bench.runners.base import register_ours

    tasks = [t for t in load_tasks(tasks_path) if t.split == split]
    if not tasks:
        raise ValueError(f"no tasks with split=={split!r} in {tasks_path}")

    store = CorpusStore(corpus_dir)
    prices = load_prices(prices_path)
    answerer, judge = (provider_factory or (lambda: _make_providers(real=real)))()

    # Read the FROZEN selection knobs (M8 Stage 1) and wire the real retrieval
    # providers into the ours* runners. app/config.py is only READ here.
    from app.config import get_settings

    settings = get_settings()
    embeddings, reranker = (retrieval_factory or _make_retrieval_providers)()
    register_ours(
        embeddings=embeddings,
        reranker=reranker,
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
    )

    rows, probes = build_sweep_rows(
        tasks=tasks,
        strategies=strategies,
        budgets=budgets,
        store=store,
        answerer=answerer,
        judge=judge,
        prices=prices,
    )

    out_dir = Path(results_root) / out_name
    retrieval_stats = {
        "rerank_api_calls": getattr(reranker, "api_calls", None),
        "rerank_cache_hits": getattr(reranker, "hits", None),
        "embed_api_calls": getattr(embeddings, "api_calls", None),
        "embed_cache_hits": getattr(embeddings, "hits", None),
    }
    report_m8.write_report(
        out_dir,
        sweep_rows=rows,
        probes=probes,
        prices=prices,
        split=split,
        budgets=tuple(budgets),
        answerer_model=answerer.model,
        judge_model=judge.model,
        settings_lambda=settings.selection_lambda,
        settings_min_value=settings.min_value,
        settings_coverage_low=settings.coverage_low,
        settings_coverage_high=settings.coverage_high,
        settings_rrf_k=settings.rrf_k,
        retrieval_stats=retrieval_stats,
    )
    return out_dir


def _parse_budgets(raw: str) -> list[int]:
    """Parse a ``2000,4000,8000`` CLI string into a list of ints."""
    return [int(part.strip()) for part in raw.split(",") if part.strip()]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the Stage 2 sweep CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.sweep",
        description=(
            "M8 Stage 2: sweep strategies over 3 budgets on a split with the "
            "REAL providers, reusing retrieval across budgets (spends keys)."
        ),
    )
    parser.add_argument("--strategies", required=True, help="comma-separated names")
    parser.add_argument("--split", default="test", choices=["dev", "test"])
    parser.add_argument("--budgets", default="2000,4000,8000", help="comma-separated")
    parser.add_argument("--out-name", default=DEFAULT_OUT_NAME)
    parser.add_argument(
        "--real",
        action="store_true",
        help="use the real providers (spends keys; human-run)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: run the sweep and print the output directory."""
    args = _parse_args(argv)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    out_dir = run_sweep(
        strategies=strategies,
        split=args.split,
        budgets=_parse_budgets(args.budgets),
        out_name=args.out_name,
        real=args.real,
    )
    print(f"wrote M8 stage 2 sweep to {out_dir}")


if __name__ == "__main__":
    main()
