"""M8 Stage 1 — grid calibration of the selection parameters (dev split only).

This is the project's DECISION POINT (ROADMAP M8). It sweeps the two SPEC §6
selection knobs over a fixed grid, measures each combination's success rate on
the DEV split with the REAL providers, picks the best combination, and derives
the coverage thresholds from the observed rerank-score distribution. The frozen
values are then written into ``app/config.py`` / ``.env.example`` by the human.

HONESTY RULE (ROADMAP M8, hard constraint): NO parameter is ever tuned by
looking at the ``test`` split. :func:`run_calibration` filters ``split == "dev"``
in code (:data:`_DEV_SPLIT`), so the test split (41 tasks) is NEVER touched.

Split of responsibilities:

* The PURE logic — :func:`pick_best` (winner selection, D6), :func:`percentiles`
  / :func:`coverage_from_scores` (coverage derivation, D7) and the :data:`GRID`
  constant — is unit-tested with synthetic data and NEVER touches the network
  (``tests/unit/bench/test_calibrate.py``).
* The REAL run — :func:`run_calibration` + :func:`main` — is a HUMAN-run utility
  (like ``bench/snapshot.py`` / ``bench/draft_tasks.py``): it constructs the real
  OpenAI/Anthropic/Jina providers, spends keys, and is NOT collected by pytest.
  The real providers are built inside the functions, never at import time, so
  importing this module in a test stays network-free.

Grid (human decision, DECISIONS.md 2026-10-13): ``lambda ∈ {0.3, 0.5, 0.7}`` ×
``min_value ∈ {0.0, 0.05, 0.1}`` = 9 combinations, calibrated ONLY at budget
4000. ``coverage_low``/``coverage_high`` are NOT in the grid; they are derived
from the ``p33``/``p66`` percentiles of the observed dev max-rerank distribution
for the winning combination.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING, Any

from bench.answer import answer
from bench.corpus_store import CorpusStore
from bench.grade import grade
from bench.report import _git_commit, model_cost_usd
from bench.run import (
    DEFAULT_CORPUS_DIR,
    DEFAULT_PRICES_PATH,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_TASKS_PATH,
    load_prices,
)
from bench.runners.ours import OursRunner, PreparedCandidates
from bench.tasks_schema import Task, load_tasks

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.llm.base import LLMProvider
    from app.providers.rerank.base import RerankProvider

#: The ONLY split the calibrator ever reads — the HONESTY RULE enforced in code.
_DEV_SPLIT = "dev"

#: Selection grid (human decision): lambda × min_value = 9 combinations.
GRID: list[tuple[float, float]] = [
    (lambda_, min_value)
    for lambda_ in (0.3, 0.5, 0.7)
    for min_value in (0.0, 0.05, 0.1)
]

#: Calibration runs ONLY at this budget (human decision).
CALIBRATION_BUDGET = 4000


@dataclass(frozen=True)
class CombinationResult:
    """Measured metrics for one ``(lambda_, min_value)`` grid combination.

    ``success_rate`` is the fraction of dev tasks graded correct. ``cost_per_
    completed_task`` is the total answerer USD cost divided by the number of
    correctly-answered tasks (``inf`` when none completed, so it never wins a
    tie-break). ``mean_context_tokens`` is the mean built-context token count.
    """

    lambda_: float
    min_value: float
    n_tasks: int
    success_rate: float
    cost_per_completed_task: float
    mean_context_tokens: float


def pick_best(results: list[CombinationResult]) -> CombinationResult:
    """Return the winning combination (D6 ordering).

    Primary key: success rate (higher wins). Tie-break 1: lowest cost per
    completed task. Tie-break 2: lowest mean context tokens. Final deterministic
    tie-break: lowest ``(lambda_, min_value)`` — so a single reproducible winner
    emerges even when every metric ties. Raises :class:`ValueError` on an empty
    input (there is no winner to pick).
    """
    if not results:
        raise ValueError("pick_best requires at least one CombinationResult")
    return min(
        results,
        key=lambda r: (
            -r.success_rate,
            r.cost_per_completed_task,
            r.mean_context_tokens,
            r.lambda_,
            r.min_value,
        ),
    )


def percentiles(
    scores: list[float], pcts: tuple[float, ...] = (0.33, 0.66)
) -> dict[str, float]:
    """Return the nearest-rank percentiles of ``scores`` keyed ``"p33"`` etc.

    Mirrors ``report._percentile``'s nearest-rank convention (rank =
    ``int(pct * (n - 1))`` over the sorted values) so the coverage derivation is
    consistent with the rest of the benchmark. An empty input maps every
    requested percentile to ``0.0``.
    """
    ordered = sorted(scores)
    out: dict[str, float] = {}
    for pct in pcts:
        key = f"p{int(round(pct * 100))}"
        if not ordered:
            out[key] = 0.0
        else:
            rank = int(pct * (len(ordered) - 1))
            out[key] = ordered[rank]
    return out


def coverage_from_scores(scores: list[float]) -> tuple[float, float]:
    """Derive ``(coverage_low, coverage_high)`` from observed rerank scores (D7).

    Splits the observed max-rerank distribution into roughly equal thirds:
    ``coverage_low = p33`` and ``coverage_high = p66``. This is an honest
    heuristic over the single observable (max rerank) and needs no labels, so it
    never consults the test split. An empty input yields the config defaults'
    spirit ``(0.0, 0.0)`` (the caller should note too few scores).
    """
    pct = percentiles(scores, (0.33, 0.66))
    return pct["p33"], pct["p66"]


def _make_providers() -> tuple[LLMProvider, LLMProvider]:
    """Construct the REAL answerer (OpenAI) + judge (Anthropic). Human-only."""
    # Imported lazily so importing this module in a test needs no SDK/key.
    from app.providers.llm.anthropic import AnthropicProvider
    from app.providers.llm.openai import OpenAIProvider

    return OpenAIProvider(), AnthropicProvider()


def _make_retrieval_providers() -> tuple[EmbeddingProvider, RerankProvider]:
    """Construct the REAL embeddings (OpenAI) + reranker (Jina), DISK-CACHED.

    Both are wrapped in the persistent, content-addressed cache
    (:mod:`bench.provider_cache`) so each distinct ``(query, documents)`` rerank
    and each distinct text embedding is computed at most once — across the 9
    grid combinations, across re-runs after an interruption, and (reusing the
    same ``bench/.cache/``) across the Stage 2 test run. This is what keeps the
    Jina quota spend to ~one rerank per task. Human-only.
    """
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    return (
        CachingEmbeddingProvider(OpenAIEmbeddingProvider()),
        CachingRerankProvider(JinaRerankProvider()),
    )


@dataclass
class _CostAccumulator:
    """Running totals for the answerer token spend (precise, priced)."""

    answerer_input_tokens: int = 0
    answerer_output_tokens: int = 0


def _measure_combination(
    lambda_: float,
    min_value: float,
    *,
    tasks: list[Task],
    prepared_by_task: dict[str, PreparedCandidates],
    store: CorpusStore,
    answerer: LLMProvider,
    judge: LLMProvider,
    prices: dict[str, Any],
    budget: int,
    cost_acc: _CostAccumulator,
) -> CombinationResult:
    """Measure one grid combination from the CACHED candidates (no rerank calls).

    ``prepared_by_task`` holds the selection-invariant pipeline output (steps
    1-5) computed ONCE per task, so this applies only the pure greedy selection
    (``lambda_``/``min_value``) + emit, then runs the benchmark's answer+grade+
    price path — identical measurement to ``bench.run`` but without re-calling
    the real reranker/embeddings 9×. Success rate from ``GradeResult.correct``;
    cost per completed task from the priced answerer calls; mean context tokens
    from the built contexts. The answerer token spend is accumulated into
    ``cost_acc`` for the overall cost report.
    """
    runner = OursRunner(lambda_=lambda_, min_value=min_value)
    corrects: list[bool] = []
    context_tokens: list[int] = []
    total_cost = 0.0
    for task in tasks:
        prepared = prepared_by_task[task.id]
        context = runner.assemble_from_candidates(prepared, budget_tokens=budget)
        answer_result = answer(task, context.text, provider=answerer)
        grade_result = grade(task, answer_result, store=store, judge_provider=judge)
        corrects.append(grade_result.correct)
        context_tokens.append(context.context_tokens)
        cost_acc.answerer_input_tokens += answer_result.input_tokens
        cost_acc.answerer_output_tokens += answer_result.output_tokens
        total_cost += model_cost_usd(
            input_tokens=answer_result.input_tokens,
            output_tokens=answer_result.output_tokens,
            model=answerer.model,
            prices=prices,
        )

    n = len(tasks)
    completed = sum(1 for ok in corrects if ok)
    return CombinationResult(
        lambda_=lambda_,
        min_value=min_value,
        n_tasks=n,
        success_rate=(completed / n) if n else 0.0,
        cost_per_completed_task=(total_cost / completed) if completed else float("inf"),
        mean_context_tokens=(mean(context_tokens) if context_tokens else 0.0),
    )


def _write_combinations_csv(out_dir: Path, results: list[CombinationResult]) -> None:
    """Write the 9-combination sweep table (sorted, fixed precision)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    header = (
        "lambda,min_value,n_tasks,success_rate,"
        "cost_per_completed_task,mean_context_tokens\n"
    )
    ordered = sorted(results, key=lambda r: (r.lambda_, r.min_value))
    lines = [header]
    for r in ordered:
        lines.append(
            f"{r.lambda_:.2f},{r.min_value:.2f},{r.n_tasks},"
            f"{r.success_rate:.4f},{r.cost_per_completed_task:.6f},"
            f"{r.mean_context_tokens:.1f}\n"
        )
    (out_dir / "combinations.csv").write_text("".join(lines), encoding="utf-8")


def _write_artifact(
    out_dir: Path,
    *,
    results: list[CombinationResult],
    winner: CombinationResult,
    coverage_low: float,
    coverage_high: float,
    coverage_pcts: dict[str, float],
    n_coverage_scores: int,
    coverage_errors: int,
    budget: int,
    n_tasks: int,
    answerer_cost: float,
    answerer_input_tokens: int,
    answerer_output_tokens: int,
    rerank_api_calls: int | None,
    rerank_cache_hits: int | None,
    embed_api_calls: int | None,
) -> None:
    """Write the calibration artifact: ``combinations.csv`` + winner + summary."""
    _write_combinations_csv(out_dir, results)

    winner_payload = {
        "selection_lambda": winner.lambda_,
        "min_value": winner.min_value,
        "coverage_low": coverage_low,
        "coverage_high": coverage_high,
        "coverage_percentiles": coverage_pcts,
        "n_coverage_scores": n_coverage_scores,
        "coverage_errors": coverage_errors,
        "success_rate": winner.success_rate,
        "cost_per_completed_task": winner.cost_per_completed_task,
        "mean_context_tokens": winner.mean_context_tokens,
        "grid": [[lambda_, min_value] for lambda_, min_value in GRID],
        "budget": budget,
        "split": _DEV_SPLIT,
        "n_tasks": n_tasks,
        "answerer_cost_usd": answerer_cost,
        "answerer_input_tokens": answerer_input_tokens,
        "answerer_output_tokens": answerer_output_tokens,
        "rerank_api_calls": rerank_api_calls,
        "rerank_cache_hits": rerank_cache_hits,
        "embed_api_calls": embed_api_calls,
        "git_commit": _git_commit(),
    }
    (out_dir / "winner.json").write_text(
        json.dumps(winner_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    summary = [
        "# M8 Stage 1 — calibration summary",
        "",
        f"- split: {_DEV_SPLIT} ONLY ({n_tasks} tasks); test split NOT touched",
        f"- budget: {budget}",
        "- grid: lambda {0.3,0.5,0.7} x min_value {0.0,0.05,0.1} (9 combos)",
        "",
        "## Winning combination",
        "",
        f"- selection_lambda = {winner.lambda_}",
        f"- min_value = {winner.min_value}",
        f"- success_rate = {winner.success_rate:.4f}",
        f"- cost_per_completed_task = {winner.cost_per_completed_task:.6f}",
        f"- mean_context_tokens = {winner.mean_context_tokens:.1f}",
        "",
        "## Coverage thresholds (derived from observed dev max-rerank, D7)",
        "",
        f"- coverage_low = p33 = {coverage_low:.4f}",
        f"- coverage_high = p66 = {coverage_high:.4f}",
        f"- scores observed: {n_coverage_scores} (tasks skipped on "
        f"provider error: {coverage_errors})",
        "",
        "## Cost (answerer, priced from prices.yaml)",
        "",
        f"- answerer input tokens: {answerer_input_tokens}",
        f"- answerer output tokens: {answerer_output_tokens}",
        f"- answerer cost (USD): {answerer_cost:.4f}",
        "",
        "## Retrieval vendor calls (persistent cache in bench/.cache/)",
        "",
        f"- rerank (Jina) real API calls: {rerank_api_calls}",
        f"- rerank cache hits: {rerank_cache_hits}",
        f"- embeddings (OpenAI) real batched API calls: {embed_api_calls}",
        "",
    ]
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def run_calibration(
    *,
    tasks_path: str | Path = DEFAULT_TASKS_PATH,
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    results_root: str | Path = DEFAULT_RESULTS_ROOT,
    prices_path: str | Path = DEFAULT_PRICES_PATH,
    budget: int = CALIBRATION_BUDGET,
    timestamp: str | None = None,
    provider_factory: Callable[[], tuple[LLMProvider, LLMProvider]] | None = None,
    retrieval_factory: (
        Callable[[], tuple[EmbeddingProvider, RerankProvider]] | None
    ) = None,
) -> Path:
    """Run the full grid calibration on the DEV split and write the artifact.

    HUMAN-run: by default builds the REAL OpenAI/Anthropic/Jina providers and
    spends keys. Loads ONLY ``split == "dev"`` tasks (the HONESTY RULE), measures
    every :data:`GRID` combination at ``budget``, picks the winner with
    :func:`pick_best`, derives coverage thresholds from the winner's observed
    max-rerank distribution (D7), and writes
    ``<results_root>/calibration_<ts>/``. Returns that directory.

    The ``*_factory`` seams exist only so the human run can be dry-tested with
    fakes if desired; they are NEVER exercised by the pytest suite.
    """
    tasks = [t for t in load_tasks(tasks_path) if t.split == _DEV_SPLIT]
    if not tasks:
        raise ValueError(f"no tasks with split=={_DEV_SPLIT!r} in {tasks_path}")

    store = CorpusStore(corpus_dir)
    prices = load_prices(prices_path)
    answerer, judge = (provider_factory or _make_providers)()
    embeddings, reranker = (retrieval_factory or _make_retrieval_providers)()

    # --- Expensive, selection-INVARIANT pass: run retrieval + rerank ONCE per
    # task (lambda_/min_value do not affect it). This is what cuts the real
    # Jina/embeddings calls from 9×96 to 1×96. A per-task failure is reported,
    # never invented (the reranker itself retries 429/5xx). The max-rerank for
    # coverage (D7) comes from the SAME cached result — no second rerank pass.
    prepare_runner = OursRunner(embeddings=embeddings, reranker=reranker)
    prepared_by_task: dict[str, PreparedCandidates] = {}
    coverage_scores: list[float] = []
    coverage_errors = 0
    for task in tasks:
        try:
            prepared = prepare_runner.prepare_candidates(task, store=store)
        except Exception as exc:  # noqa: BLE001 — report, never invent.
            coverage_errors += 1
            print(f"prepare: task {task.id} failed, skipped: {exc}")
            continue
        prepared_by_task[task.id] = prepared
        if prepared.max_rerank is not None:
            coverage_scores.append(prepared.max_rerank)

    measured_tasks = [t for t in tasks if t.id in prepared_by_task]

    # Report how many REAL rerank/embedding vendor calls were actually made
    # (the rest were served from the persistent cache).
    rerank_api_calls = getattr(reranker, "api_calls", None)
    rerank_cache_hits = getattr(reranker, "hits", None)
    embed_api_calls = getattr(embeddings, "api_calls", None)

    cost_acc = _CostAccumulator()
    results = [
        _measure_combination(
            lambda_,
            min_value,
            tasks=measured_tasks,
            prepared_by_task=prepared_by_task,
            store=store,
            answerer=answerer,
            judge=judge,
            prices=prices,
            budget=budget,
            cost_acc=cost_acc,
        )
        for lambda_, min_value in GRID
    ]
    winner = pick_best(results)

    coverage_low, coverage_high = coverage_from_scores(coverage_scores)
    coverage_pcts = percentiles(coverage_scores, (0.33, 0.66))

    answerer_cost = model_cost_usd(
        input_tokens=cost_acc.answerer_input_tokens,
        output_tokens=cost_acc.answerer_output_tokens,
        model=answerer.model,
        prices=prices,
    )

    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    out_dir = Path(results_root) / f"calibration_{timestamp}"
    _write_combinations_csv(out_dir, results)
    _write_artifact(
        out_dir,
        results=results,
        winner=winner,
        coverage_low=coverage_low,
        coverage_high=coverage_high,
        coverage_pcts=coverage_pcts,
        n_coverage_scores=len(coverage_scores),
        coverage_errors=coverage_errors,
        budget=budget,
        n_tasks=len(measured_tasks),
        answerer_cost=answerer_cost,
        answerer_input_tokens=cost_acc.answerer_input_tokens,
        answerer_output_tokens=cost_acc.answerer_output_tokens,
        rerank_api_calls=rerank_api_calls,
        rerank_cache_hits=rerank_cache_hits,
        embed_api_calls=embed_api_calls,
    )
    return out_dir


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the CLI arguments for the human calibration run."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.calibrate",
        description=(
            "M8 Stage 1: grid-calibrate the selection params on the DEV split "
            "with the REAL providers (spends keys; human-run)."
        ),
    )
    parser.add_argument(
        "--real",
        action="store_true",
        required=True,
        help="confirm the REAL providers run (spends keys; dev split only)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=CALIBRATION_BUDGET,
        help="context token budget (human decision: calibrate only at 4000)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: run the real dev-split calibration and print the path."""
    args = _parse_args(argv)
    out_dir = run_calibration(budget=args.budget)
    print(f"wrote calibration artifact to {out_dir}")


if __name__ == "__main__":
    main()
