"""M10a Phase 3, item 1: why NINA's selection stops early, and what fixes it.

Exploration only: it swaps the ``select`` the ours runner calls for a
parameterized copy, so no ``app/`` code changes until a variant is accepted.
A variant is ``(lambda_, min_value, gate)``: the current selector is
``gate = -inf``; a finite ``gate`` lets only candidates whose rerank value is at
least ``gate`` compete, so the diversity penalty orders them but never ends the
selection by itself.

Candidates are prepared once per task through the disk-cached providers (no new
spend expected; the paid-call counts are printed) and every variant reuses them.

Usage:
    uv run python -m bench.m10a_selection --half A
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

import bench.runners.ours as ours_module
from app.config import get_settings
from app.retrieve.select import Candidate, _adjusted_value
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.runners.ours import OursRunner
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: name -> (lambda_, min_value, gate)
VARIANTS: dict[str, tuple[float, float, float]] = {
    "current": (0.3, 0.0, -math.inf),
    "lambda_0.2": (0.2, 0.0, -math.inf),
    "lambda_0.1": (0.1, 0.0, -math.inf),
    "lambda_0": (0.0, 0.0, -math.inf),
    "no_value_stop": (0.3, -math.inf, -math.inf),
    "gate_0.1": (0.3, -math.inf, 0.1),
    "gate_0.2": (0.3, -math.inf, 0.2),
    "gate_0.3": (0.3, -math.inf, 0.3),
}
_OUT = Path("bench/results/m10a")


def select_variant(
    candidates: Sequence[Candidate],
    *,
    budget_tokens: int,
    lambda_: float,
    min_value: float,
    gate: float = -math.inf,
    reasons: list[str] | None = None,
) -> list[Candidate]:
    """``app.retrieve.select.select`` plus a value gate; records the stop reason."""
    remaining = list(candidates)
    selected: list[Candidate] = []
    budget_left = budget_tokens
    reason = "pool_exhausted"
    while remaining:
        fitting = [c for c in remaining if 0 < c.tokens <= budget_left]
        if not fitting:
            reason = "budget_full"
            break
        eligible = [c for c in fitting if c.value >= gate]
        if not eligible:
            reason = "gate"
            break
        best = max(eligible, key=lambda c: _adjusted_value(c, selected, lambda_))
        if _adjusted_value(best, selected, lambda_) < min_value:
            reason = "adjusted_value_below_min"
            break
        selected.append(best)
        budget_left -= best.tokens
        remaining.remove(best)
    if reasons is not None:
        reasons.append(reason)
    selected.sort(key=lambda c: (c.document_id, c.ord))
    return selected


def _patched(gate: float, reasons: list[str]) -> Callable[..., list[Candidate]]:
    def select(candidates, *, budget_tokens, lambda_, min_value):  # type: ignore[no-untyped-def]
        return select_variant(
            candidates,
            budget_tokens=budget_tokens,
            lambda_=lambda_,
            min_value=min_value,
            gate=gate,
            reasons=reasons,
        )

    return select


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    args = parser.parse_args(argv)

    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CachingRerankProvider(JinaRerankProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    keys = [
        k
        for k in map(
            json.loads, (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    settings = get_settings()
    base = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    prepared = []
    for i, key in enumerate(keys, 1):
        prepared.append(
            (
                key,
                base.prepare_candidates(bench_task(tasks[key["task_id"]]), store=store),
            )
        )
        if i % 25 == 0:
            print(f"prepared {i}/{len(keys)}", flush=True)

    lines = [
        f"# M10a selection variants, half {args.half} ({len(keys)} tasks)",
        "",
        "| variant | " + " | ".join(f"hit {b} | tokens {b}" for b in BUDGETS) + " |",
        "|---" * (1 + 2 * len(BUDGETS)) + "|",
    ]
    original = ours_module.select
    stop_counts: dict[str, Counter[str]] = {}
    try:
        for name in args.variants.split(","):
            lambda_, min_value, gate = VARIANTS[name]
            runner = OursRunner(
                lambda_=lambda_,
                min_value=min_value,
                lexical_weight=settings.lexical_weight,
                embeddings=embeddings,
                reranker=reranker,
            )
            cells = []
            for budget in BUDGETS:
                reasons: list[str] = []
                ours_module.select = _patched(gate, reasons)  # type: ignore[assignment]
                hits, tokens = [], []
                for key, prep in prepared:
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    hits.append(coverage(units(key["section"]), result.text) >= HIT)
                    tokens.append(result.context_tokens)
                stop_counts[f"{name} {budget}"] = Counter(reasons)
                cells.append(
                    f"{sum(hits) / len(hits):.3f} | {statistics.mean(tokens):,.0f}"
                )
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    finally:
        ours_module.select = original
    lines += ["", "## Why the selection stopped", ""]
    lines += [
        f"- {k}: {dict(v)}" for k, v in stop_counts.items() if k.startswith("current")
    ]
    report = "\n".join(lines) + "\n"
    (_OUT / f"selection_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
