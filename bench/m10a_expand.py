"""M10a Phase 3, item 3: answer sections split across chunks.

The chunker closes a chunk once it reaches 200 tokens and never crosses a
heading, so a 500-token section ends up in two or three chunks and the selector
may take only one. Here, each chunk the selector picks pulls in its neighbors in
the same document (``ord`` ± 1 … ± ``window``) while they fit the budget,
optionally only while they share the picked chunk's heading path. Neighbors come
from the task's index, so no new provider call is needed (paid-call counts are
printed); the selection knobs are the frozen ``app.config`` defaults, not the
local ``.env``.

Usage:
    uv run python -m bench.m10a_expand --half A
    uv run python -m bench.m10a_expand --half B --confirm adj_1_heading
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

import bench.runners.ours as ours_module
from app.config import Settings
from app.retrieve.select import Candidate, _adjusted_value
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_selection import _strong_rag, confirm
from bench.runners.ours import OursRunner, _IndexedChunk, _Key
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: name -> (window, neighbors must share the picked chunk's heading path,
#: picks expanded (None: all), smallest budget that expands)
VARIANTS: dict[str, tuple[int, bool, int | None, int]] = {
    "current": (0, True, None, 0),
    "adj_1_heading": (1, True, None, 0),
    "adj_1": (1, False, None, 0),
    "adj_2_heading": (2, True, None, 0),
    "adj_3_heading": (3, True, None, 0),
    "adj_1_top1": (1, False, 1, 0),
    "adj_1_heading_top1": (1, True, 1, 0),
    "adj_1_from_4k": (1, False, None, 4_000),
    "adj_1_heading_from_4k": (1, True, None, 4_000),
}
_OUT = Path("bench/results/m10a")

Neighbors = Callable[[Candidate], list[Candidate]]


def neighbors_by_ord(
    indexed: Sequence[_IndexedChunk], window: int, same_heading: bool
) -> Neighbors:
    """Neighbors of a chunk, nearest first and the following one before the
    preceding one; a direction stops at a gap or, if asked, a heading change."""
    by_key = {item.key: item for item in indexed}

    def neighbors(picked: Candidate) -> list[Candidate]:
        origin = by_key.get((picked.document_id, picked.ord))
        found = []
        for step in (1, -1):
            for d in range(1, window + 1):
                item = by_key.get((picked.document_id, picked.ord + step * d))
                if item is None or (
                    same_heading
                    and origin is not None
                    and item.chunk.heading_path != origin.chunk.heading_path
                ):
                    break
                found.append((d, step != 1, item))
        found.sort(key=lambda f: (f[0], f[1]))
        return [
            Candidate(chunk_id=0, document_id=item.document_id, ord=item.ord,
                      value=0.0, tokens=item.chunk.tokens, embedding=item.embedding)
            for _, _, item in found
        ]  # fmt: skip

    return neighbors


def select_expand(
    candidates: Sequence[Candidate],
    *,
    budget_tokens: int,
    lambda_: float,
    min_value: float,
    neighbors: Neighbors,
    expand_picks: int | None = None,
) -> list[Candidate]:
    """``app.retrieve.select.select`` plus: each pick (the first
    ``expand_picks`` picks, if given) pulls in its neighbors."""
    remaining = list(candidates)
    selected: list[Candidate] = []
    chosen: set[_Key] = set()
    budget_left = budget_tokens
    picks = 0

    def take(c: Candidate) -> None:
        nonlocal budget_left
        selected.append(c)
        chosen.add((c.document_id, c.ord))
        budget_left -= c.tokens
        remaining[:] = [r for r in remaining if (r.document_id, r.ord) not in chosen]

    while remaining:
        fitting = [c for c in remaining if 0 < c.tokens <= budget_left]
        if not fitting:
            break
        best = max(fitting, key=lambda c: _adjusted_value(c, selected, lambda_))
        if _adjusted_value(best, selected, lambda_) < min_value:
            break
        take(best)
        picks += 1
        if expand_picks is not None and picks > expand_picks:
            continue
        for n in neighbors(best):
            if (n.document_id, n.ord) not in chosen and 0 < n.tokens <= budget_left:
                take(n)
    selected.sort(key=lambda c: (c.document_id, c.ord))
    return selected


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--confirm", help="variant to test against current")
    args = parser.parse_args(argv)
    if args.confirm:
        args.variants = f"current,{args.confirm}"

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
    frozen = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = OursRunner(
        lambda_=frozen.selection_lambda,
        min_value=frozen.min_value,
        lexical_weight=frozen.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    prepared = []
    for i, key in enumerate(keys, 1):
        task = bench_task(tasks[key["task_id"]])
        start = perf_counter()
        prep = runner.prepare_candidates(task, store=store)
        prepare_ms = (perf_counter() - start) * 1000
        indexed, _ = runner._index_task(task, store)
        prep = dataclasses.replace(
            prep, md_by_key={item.key: item.chunk.md for item in indexed}
        )
        prepared.append((key, prep, indexed, prepare_ms))
        if i % 25 == 0:
            print(f"prepared {i}/{len(keys)}", flush=True)

    lines = [
        f"# M10a neighbor expansion, half {args.half} ({len(keys)} tasks)",
        "",
        "| variant | " + " | ".join(f"hit {b} | tokens {b}" for b in BUDGETS) + " |",
        "|---" * (1 + 2 * len(BUDGETS)) + "|",
    ]
    original = ours_module.select
    measured: dict[str, dict[int, dict[str, list[Any]]]] = {}
    try:
        for name in args.variants.split(","):
            window, same_heading, expand_picks, min_budget = VARIANTS[name]
            cells = []
            for budget in BUDGETS:
                cell: dict[str, list[Any]] = {
                    "hit": [], "coverage": [], "tokens": [], "ms": []
                }  # fmt: skip
                for key, prep, indexed, prepare_ms in prepared:
                    neighbors = neighbors_by_ord(
                        indexed, window if budget >= min_budget else 0, same_heading
                    )

                    def patched(candidates, *, budget_tokens, lambda_, min_value,  # type: ignore[no-untyped-def]
                                _n=neighbors, _k=expand_picks):  # fmt: skip
                        return select_expand(
                            candidates, budget_tokens=budget_tokens,
                            lambda_=lambda_, min_value=min_value, neighbors=_n,
                            expand_picks=_k,
                        )  # fmt: skip

                    ours_module.select = patched  # type: ignore[assignment]
                    start = perf_counter()
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    cov = coverage(units(key["section"]), result.text)
                    cell["ms"].append(prepare_ms + (perf_counter() - start) * 1000)
                    cell["hit"].append(cov >= HIT)
                    cell["coverage"].append(cov)
                    cell["tokens"].append(result.context_tokens)
                measured.setdefault(name, {})[budget] = cell
                cells.append(
                    f"{statistics.mean(cell['hit']):.3f} | "
                    f"{statistics.mean(cell['tokens']):,.0f}"
                )
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    finally:
        ours_module.select = original
    out = f"expand_{args.half}"
    if args.confirm:
        out = f"confirm_{args.confirm}_{args.half}"
        strong = _strong_rag([key["task_id"] for key, *_ in prepared])
        checks = confirm(measured[args.confirm], measured["current"], strong)
        lines += ["", f"## Acceptance: {args.confirm} vs current", ""]
        lines += [
            f"- {'PASS' if ok else 'FAIL'} {what}: {detail}"
            for what, ok, detail in checks
        ]
        verdict = "ACCEPTED" if all(ok for _, ok, _ in checks) else "REJECTED"
        lines += ["", f"**{verdict}**"]
    report = "\n".join(lines) + "\n"
    (_OUT / f"{out}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
