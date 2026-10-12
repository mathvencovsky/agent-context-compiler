"""Benchmark CLI: run strategies over the corpus and write a report (SPEC §10).

Wires the frozen :class:`~bench.corpus_store.CorpusStore`, the baseline runners,
the answerer and judge providers, and the report aggregator into one command::

    python -m bench.run --strategies raw_html,naive_text,naive_rag --split dev

For every ``(task, strategy)`` it builds a bounded context (SPEC §10.3), answers
it with the answerer at temperature 0 (``bench.answer``), grades the answer with
the judge (``bench.grade``), prices the call from ``bench/prices.yaml``, and
emits one :class:`~bench.report.ResultRow`. :func:`run` then aggregates the rows
into ``bench/results/<timestamp>/`` (SPEC §10.5).

By default both providers are the deterministic fake, so a run is reproducible
and never touches the network — this is what the e2e test and the acceptance
criteria exercise. The real OpenAI/Anthropic providers are selected only for a
human run (``--real``) and are never used by tests.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLMProvider
from bench.answer import answer
from bench.corpus_store import CorpusStore
from bench.grade import grade
from bench.report import ResultRow, aggregate, model_cost_usd
from bench.runners.base import get_runner
from bench.tasks_schema import Task, load_tasks

#: Repository paths (bench/ lives at the repo's bench directory).
_BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_DIR = _BENCH_DIR / "corpus"
# SPEC §2: tasks live at bench/tasks/tasks.yaml (same path review_tasks and
# run_draft use); the previous bench/tasks.yaml here was a path mismatch.
DEFAULT_TASKS_PATH = _BENCH_DIR / "tasks" / "tasks.yaml"
DEFAULT_PRICES_PATH = _BENCH_DIR / "prices.yaml"
DEFAULT_RESULTS_ROOT = _BENCH_DIR / "results"

#: Env overrides used by the e2e test to point the CLI at the fixture corpus
#: without touching the real (gitignored) corpus or spending keys.
ENV_CORPUS_DIR = "BENCH_CORPUS_DIR"
ENV_TASKS_PATH = "BENCH_TASKS_PATH"
ENV_RESULTS_ROOT = "BENCH_RESULTS_ROOT"

#: Default per-strategy context budget in tokens (overridable via --budget).
DEFAULT_BUDGET = 8000


def load_prices(path: str | Path = DEFAULT_PRICES_PATH) -> dict[str, Any]:
    """Parse ``bench/prices.yaml`` into a plain dict."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _make_providers(*, real: bool) -> tuple[LLMProvider, LLMProvider]:
    """Return ``(answerer, judge)`` providers.

    The default is the deterministic fake for BOTH roles (tests, reproducible
    runs). ``real=True`` constructs the real OpenAI answerer and Anthropic judge
    — a human-only path that spends keys and is never used by tests.
    """
    if not real:
        return FakeLLMProvider(model="fake-answerer"), FakeLLMProvider(
            model="fake-judge"
        )
    # Human-only: real providers reach the network and read BENCH_* keys.
    from app.providers.llm.anthropic import AnthropicProvider  # pragma: no cover
    from app.providers.llm.openai import OpenAIProvider  # pragma: no cover

    return OpenAIProvider(), AnthropicProvider()  # pragma: no cover


def _grade_one(
    task: Task,
    strategy: str,
    *,
    budget: int,
    store: CorpusStore,
    answerer: LLMProvider,
    judge: LLMProvider,
    prices: dict[str, Any],
) -> ResultRow:
    """Build context, answer, grade and price one ``(task, strategy)`` pair."""
    runner = get_runner(strategy)
    start = time.perf_counter()
    context = runner.build_context(task, budget_tokens=budget, store=store)
    answer_result = answer(task, context.text, provider=answerer)
    grade_result = grade(task, answer_result, store=store, judge_provider=judge)
    latency_ms = (time.perf_counter() - start) * 1000.0

    cost = model_cost_usd(
        input_tokens=answer_result.input_tokens,
        output_tokens=answer_result.output_tokens,
        model=answerer.model,
        prices=prices,
    )

    return ResultRow(
        task_id=task.id,
        strategy=strategy,
        category=task.category,
        split=task.split,
        answer_type=task.answer_type,
        context_tokens=context.context_tokens,
        input_tokens=answer_result.input_tokens,
        output_tokens=answer_result.output_tokens,
        correct=grade_result.correct,
        citation_correct=grade_result.citation_correct,
        is_insufficient=grade_result.is_insufficient,
        cost_usd=cost,
        latency_ms=latency_ms,
        validated=task.validated,
    )


def run(
    *,
    strategies: list[str],
    split: str,
    budget: int = DEFAULT_BUDGET,
    corpus_dir: str | Path | None = None,
    tasks_path: str | Path | None = None,
    results_root: str | Path | None = None,
    prices_path: str | Path = DEFAULT_PRICES_PATH,
    real: bool = False,
    timestamp: str | None = None,
    provider_factory: Callable[[], tuple[LLMProvider, LLMProvider]] | None = None,
) -> Path:
    """Run every ``(task, strategy)`` for ``split`` and write the report.

    Paths fall back to the ``BENCH_*`` env overrides and then to the ``bench/``
    defaults, so the e2e test can redirect the CLI at the fixture corpus without
    arguments. ``provider_factory`` (default: the fake pair, or the real pair
    when ``real=True``) builds the answerer/judge; tests always use the fake.
    Returns the output directory.
    """
    corpus_dir = corpus_dir or os.environ.get(ENV_CORPUS_DIR) or DEFAULT_CORPUS_DIR
    tasks_path = tasks_path or os.environ.get(ENV_TASKS_PATH) or DEFAULT_TASKS_PATH
    results_root = (
        results_root or os.environ.get(ENV_RESULTS_ROOT) or DEFAULT_RESULTS_ROOT
    )

    store = CorpusStore(corpus_dir)
    tasks = [t for t in load_tasks(tasks_path) if t.split == split]
    prices = load_prices(prices_path)

    if provider_factory is None:
        answerer, judge = _make_providers(real=real)
    else:
        answerer, judge = provider_factory()

    rows: list[ResultRow] = []
    for strategy in strategies:
        for task in tasks:
            rows.append(
                _grade_one(
                    task,
                    strategy,
                    budget=budget,
                    store=store,
                    answerer=answerer,
                    judge=judge,
                    prices=prices,
                )
            )

    return aggregate(
        rows,
        prices=prices,
        budget=budget,
        split=split,
        results_root=results_root,
        timestamp=timestamp,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.run",
        description="Run benchmark strategies over the frozen corpus (SPEC §10).",
    )
    parser.add_argument(
        "--strategies",
        required=True,
        help="comma-separated strategy names, e.g. raw_html,naive_text,naive_rag",
    )
    parser.add_argument(
        "--split", default="dev", choices=["dev", "test"], help="task split to run"
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help="per-strategy context token budget",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="use the real OpenAI/Anthropic providers (spends keys; human-run)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: parse args, run, and print the output directory."""
    args = _parse_args(argv)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    out_dir = run(
        strategies=strategies,
        split=args.split,
        budget=args.budget,
        real=args.real,
    )
    print(f"wrote benchmark report to {out_dir}")


if __name__ == "__main__":
    main()
