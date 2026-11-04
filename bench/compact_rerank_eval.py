"""Phase 2: does choosing sentences with the local reranker beat BM25 in ``compact``?

Half A only (dev slice, candidates from the disk cache, a miss stops with
``PaidCallRefused``). The local Qwen3 reranker scores each unit of the pool against
the question; a unit below ``floor`` (raw score) gets 0 of its own, and can still
come along as the neighbour of a hit on its line (the shipped window). The
BM25 arm is ``compact`` as shipped. ``answer_hit`` and the budgets are the ones of
:mod:`bench.compact_eval`.

**Acceptance rule (written before measuring, DECISIONS.md 2026-11-04).** A floor is
accepted if the median reranking time per question is at most
:data:`MAX_MEDIAN_SECONDS` and either:

a. ``answer_hit`` at 1000 is at least 5 points above BM25 at 1000, at no more
   tokens; or
b. ``answer_hit`` at 500 is within 1 point of BM25 at 1000 (same answers, half
   the text).

Among accepted floors, the fewest mean tokens at the budget that passed wins. No
floor passing means BM25 stays and sentence reranking is dropped. A pass here is
evidence on documentation only, and the human decides on the product change.

Usage:
    uv run python -m bench.compact_rerank_eval --reranker-url http://127.0.0.1:8081
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from bench.compact_eval import BUDGETS, answer_hit, compact_context, per_1k

FLOORS = (0.0, 0.1, 0.5)
MAX_MEDIAN_SECONDS = 10.0
_OUT = Path("bench/results/m10a")


def floored(
    score: Callable[[Sequence[str]], Sequence[float]], floor: float
) -> Callable[[Sequence[str]], list[float]]:
    """``score`` with every value below ``floor`` turned into 0 (never kept)."""

    def scorer(texts: Sequence[str]) -> list[float]:
        return [s if s >= floor else 0.0 for s in score(texts)]

    return scorer


def cached_scorer(
    rerank: Callable[[str, list[str]], list[float]],
    question: str,
    seconds: list[float],
) -> Callable[[Sequence[str]], list[float]]:
    """Score unit texts once per question, recording the time of each call."""
    known: dict[str, float] = {}

    def score(texts: Sequence[str]) -> list[float]:
        missing = [t for t in dict.fromkeys(texts) if t not in known]
        if missing:
            started = time.perf_counter()
            known.update(zip(missing, rerank(question, missing), strict=True))
            seconds.append(time.perf_counter() - started)
        return [known[t] for t in texts]

    return score


def accepted(
    cells: dict[str, dict[int, dict[str, list[float]]]], median_seconds: float
) -> list[tuple[str, str, float]]:
    """``(arm, reason, tokens)`` of the arms the rule accepts, fewest tokens first."""
    if median_seconds > MAX_MEDIAN_SECONDS:
        return []
    base = cells["bm25"][1_000]
    base_hit = statistics.mean(base["answer_hit"])
    base_tokens = statistics.mean(base["tokens"])
    out = []
    for arm, by_budget in cells.items():
        if arm == "bm25":
            continue
        hit = statistics.mean(by_budget[1_000]["answer_hit"])
        tokens = statistics.mean(by_budget[1_000]["tokens"])
        if round(hit - base_hit, 6) >= 0.05 and tokens <= base_tokens:
            out.append((arm, "a", tokens))
        half = by_budget[500]
        if round(statistics.mean(half["answer_hit"]) - base_hit, 6) >= -0.01:
            out.append((arm, "b", statistics.mean(half["tokens"])))
    return sorted(out, key=lambda row: row[2])


def main() -> None:  # pragma: no cover - real corpus and local model
    from app.config import get_settings
    from app.index.tokens import count_tokens
    from app.providers.rerank.local import JinaCompatibleRerankProvider
    from app.retrieve.compact import pool_budget
    from app.retrieve.select import select
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import bench_task
    from bench.m10b_retrieval import PaidCallRefused, cached_jina, cached_openai
    from bench.runners.base import truncate_to_budget
    from bench.runners.ours import OursRunner
    from bench.section_recall import coverage, units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reranker-url", required=True)
    parser.add_argument(
        "--reranker-model", default="Qwen3-Reranker-0.6B-Q8_0@ggml-org-GGUF"
    )
    args = parser.parse_args()

    reranker = JinaCompatibleRerankProvider(
        base_url=args.reranker_url, model=args.reranker_model, score_transform="none"
    )
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    lines = (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [k for k in map(json.loads, lines) if in_tune_half(k["task_id"])]
    settings = get_settings()
    runner = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=cached_openai(),
        reranker=cached_jina(),
    )
    cells: dict[str, dict[int, dict[str, list[float]]]] = {}
    seconds: list[float] = []
    skipped = 0

    def record(arm: str, budget: int, ctx: str, su: list[str]) -> None:
        cell = cells.setdefault(arm, {}).setdefault(
            budget, {"answer_hit": [], "coverage": [], "tokens": []}
        )
        cell["answer_hit"].append(float(answer_hit(su, ctx)))
        cell["coverage"].append(coverage(su, ctx))
        cell["tokens"].append(float(count_tokens(ctx)))

    for done, key in enumerate(keys):
        if done % 10 == 0:
            print(f"{done}/{len(keys)}", flush=True)
        task = bench_task(tasks[key["task_id"]])
        try:
            prep = runner.prepare_candidates(task, store=store)
        except PaidCallRefused:  # extraction fixes changed a chunk: never pay
            skipped += 1
            continue
        su = units(key["section"])
        score = cached_scorer(reranker.rerank, task.question, seconds)

        for budget in BUDGETS[:2]:
            pool = select(
                prep.candidates,
                budget_tokens=pool_budget(budget),
                lambda_=settings.selection_lambda,
                min_value=settings.min_value,
            )
            base = compact_context(
                task.question, pool, prep.md_by_key, prep.url_by_document, budget
            )
            record("bm25", budget, truncate_to_budget(base, budget)[0], su)
            for floor in FLOORS:
                ctx = compact_context(
                    task.question,
                    pool,
                    prep.md_by_key,
                    prep.url_by_document,
                    budget,
                    unit_scorer=floored(score, floor),
                )
                record(f"qwen_{floor}", budget, truncate_to_budget(ctx, budget)[0], su)

    median_seconds = statistics.median(seconds)
    measured = len(keys) - skipped
    out = [
        f"# Sentence choice by local reranker, half A ({measured} tasks, "
        f"{skipped} left out: not in the cache)",
        "",
    ]
    out += [
        "| arm | budget | answer_hit | recall/1k | tokens |",
        "|---|---|---|---|---|",
    ]
    for arm, by_budget in cells.items():
        for budget, cell in by_budget.items():
            out.append(
                f"| {arm} | {budget} | {statistics.mean(cell['answer_hit']):.3f} "
                f"| {per_1k(cell):.3f} | {statistics.mean(cell['tokens']):,.0f} |"
            )
    rows = accepted(cells, median_seconds)
    out += ["", f"Median reranking per call: {median_seconds:.1f} s"]
    out += [f"**Accepted: {rows}**" if rows else "**REJECTED: no arm passes the rule**"]
    report = "\n".join(out) + "\n"
    (_OUT / "compact_rerank_A.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
