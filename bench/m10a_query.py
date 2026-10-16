"""M10a Phase 3, item 2b: clean the long question before searching.

The answer chunk misses the rerank pool mostly because the query is the whole
Stack Overflow question (title + body with pasted code and filler). This module
strips code-like lines and filler phrases from the body and ranks the pool with
the cleaned query, on the lexical side only or on both sides (new query
embeddings: paid OpenAI calls, counted). Ranking only: no rerank calls.

Usage:
    uv run python -m bench.m10a_query --half A --data-root <checkout with data>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import get_settings
from app.retrieve.rrf import rrf_fuse
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_diagnose import paired
from bench.m10a_pool import pool_hits
from bench.m10a_selection import _strong_rag, confirm
from bench.runners.ours import _RRF_K, OursRunner, _lexical_ranks, _vector_ranks
from bench.section_recall import HIT, coverage, units
from bench.tasks_schema import load_external_tasks

_OUT = Path("bench/results/m10a")

_SYMBOLS = frozenset("{}[]()<>=;\\$&|`\"'#@*/_")
#: A line is code when at least this share of its non-space characters are symbols.
_CODE_SYMBOL_SHARE = 0.12
_FILLER = re.compile(
    r"enter image description here|thanks? (you )?in advance|thank you|"
    r"new to stack ?overflow|any help (would be|is) (greatly |much )?appreciated",
    re.IGNORECASE,
)
#: Words of cleaned body kept by the ``lead`` variant.
_LEAD_WORDS = 60


def _is_code(line: str) -> bool:
    """Indented lines and lines dense in code symbols count as code."""
    if line.startswith(("    ", "\t")):
        return True
    chars = [c for c in line if not c.isspace()]
    return bool(chars) and sum(c in _SYMBOLS for c in chars) / len(chars) >= (
        _CODE_SYMBOL_SHARE
    )


def clean_body(body: str) -> str:
    """The body without code-like lines and Stack Overflow filler phrases."""
    kept = [_FILLER.sub("", line) for line in body.splitlines() if not _is_code(line)]
    return "\n".join(line for line in kept if line.strip())


def lead(text: str, words: int = _LEAD_WORDS) -> str:
    """The first ``words`` words of ``text``."""
    return " ".join(text.split()[:words])


class _SearchSwap:
    """Embeddings that embed ``swap[text]`` in place of ``text`` (the question)."""

    def __init__(self, delegate: Any, swap: dict[str, str]) -> None:
        self._delegate = delegate
        self.model = delegate.model
        self.dim = delegate.dim
        self.swap = swap

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._delegate.embed([self.swap.get(t, t) for t in texts])


def _full(
    keys: list[dict[str, Any]],
    tasks: dict[str, Any],
    store: CorpusStore,
    embeddings: Any,
    reranker: Any,
    with_rule: bool = False,
) -> list[str]:  # pragma: no cover - real corpus
    """Full pipeline, current vs ``strip`` (cleaned query for search only).

    The reranker still sees the full question, so the pool is exactly the one
    ``strip`` measured above; only the pools that changed cost rerank calls.
    """
    import bench.runners.ours as ours_module

    settings = get_settings()
    swap: dict[str, str] = {}
    runner = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=_SearchSwap(embeddings, swap),
        reranker=reranker,
    )
    original = ours_module._lexical_ranks
    ours_module._lexical_ranks = lambda indexed, query: original(
        indexed, swap.get(query, query)
    )
    names = ("current", "strip")
    measured: dict[str, dict[int, dict[str, list[Any]]]] = {
        name: {b: {"hit": [], "coverage": [], "tokens": [], "ms": []} for b in BUDGETS}
        for name in names
    }
    try:
        for i, key in enumerate(keys, 1):
            ext = tasks[key["task_id"]]
            task = bench_task(ext)
            section_units = units(key["section"])
            for name in names:
                swap.clear()
                if name == "strip":
                    swap[task.question] = (
                        f"{ext.question_title}\n\n{clean_body(ext.question)}"
                    )
                start = perf_counter()
                prep = runner.prepare_candidates(task, store=store)
                prepare_ms = (perf_counter() - start) * 1000
                for budget in BUDGETS:
                    start = perf_counter()
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    cov = coverage(section_units, result.text)
                    cell = measured[name][budget]
                    cell["ms"].append(prepare_ms + (perf_counter() - start) * 1000)
                    cell["hit"].append(cov >= HIT)
                    cell["coverage"].append(cov)
                    cell["tokens"].append(result.context_tokens)
            if i % 25 == 0:
                print(f"assembled {i}/{len(keys)}", flush=True)
    finally:
        ours_module._lexical_ranks = original

    lines = [
        "",
        f"## Full pipeline (lambda {settings.selection_lambda}), hit and tokens",
        "",
        "| variant | " + " | ".join(f"hit {b} | tokens {b}" for b in BUDGETS) + " |",
        "|---" * (1 + 2 * len(BUDGETS)) + "|",
    ]
    for name in names:
        cells = [
            f"{statistics.mean(measured[name][b]['hit']):.3f} | "
            f"{statistics.mean(measured[name][b]['tokens']):,.0f}"
            for b in BUDGETS
        ]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "strip vs current, paired (diff [95% CI], McNemar p):", ""]
    for b in BUDGETS:
        p = paired(measured["strip"][b]["hit"], measured["current"][b]["hit"])
        lines.append(
            f"- {b}: {p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}], p={p['p']:.4f}"
        )
    if with_rule:
        strong = _strong_rag([key["task_id"] for key in keys])
        checks = confirm(measured["strip"], measured["current"], strong)
        lines += ["", "## Acceptance: strip vs current (DECISIONS 2026-10-16)", ""]
        lines += [
            f"- {'PASS' if ok else 'FAIL'} {what}: {detail}"
            for what, ok, detail in checks
        ]
        verdict = "ACCEPTED" if all(ok for _, ok, _ in checks) else "REJECTED"
        lines += ["", f"**{verdict}**"]
    return lines


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument(
        "--data-root", type=Path, default=Path("."),
        help="checkout holding bench/corpus, bench/.cache and the qrels",
    )  # fmt: skip
    parser.add_argument(
        "--full", action="store_true",
        help="also run the full pipeline for strip (paid rerank calls)",
    )  # fmt: skip
    args = parser.parse_args(argv)

    root = args.data_root / "bench"
    embeddings = CachingEmbeddingProvider(
        OpenAIEmbeddingProvider(), cache_dir=root / ".cache"
    )
    store = CorpusStore(root / "corpus")
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    qrels = (root / "results/m10a/qrels.jsonl").read_text(encoding="utf-8")
    keys = [
        k
        for k in map(json.loads, qrels.splitlines())
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    if args.full:
        reranker = CachingRerankProvider(
            JinaRerankProvider(), cache_dir=root / ".cache"
        )
        lines = [f"# M10a query cleaning, half {args.half} ({len(keys)} tasks)"]
        lines += _full(
            keys, tasks, store, embeddings, reranker, with_rule=args.half == "B"
        )
        report = "\n".join(lines) + "\n"
        (_OUT / f"query_full_{args.half}.md").write_text(report, encoding="utf-8")
        print(report)
        print(
            f"paid calls: embeddings {embeddings.api_calls}, "
            f"rerank {reranker.api_calls}"
        )
        return
    runner = OursRunner(embeddings=embeddings)
    weight = get_settings().lexical_weight
    # name -> (lexical query, vector query)
    names = ["current", "strip_lex", "strip", "lead"]
    counts: dict[str, Counter[str]] = {name: Counter() for name in names}
    for i, key in enumerate(keys, 1):
        ext = tasks[key["task_id"]]
        task = bench_task(ext)
        full = task.question
        stripped = f"{ext.question_title}\n\n{clean_body(ext.question)}"
        short = f"{ext.question_title}\n\n{lead(clean_body(ext.question))}"
        queries = {
            "current": (full, full),
            "strip_lex": (stripped, full),
            "strip": (stripped, stripped),
            "lead": (short, short),
        }
        texts = sorted({v for pair in queries.values() for v in pair})
        vector_by_text = dict(zip(texts, embeddings.embed(texts), strict=True))
        indexed, _ = runner._index_task(task, store)
        md_by_key = {item.key: item.chunk.md for item in indexed}
        section_units = units(key["section"])
        for name, (lex_q, vec_q) in queries.items():
            lex = _lexical_ranks(indexed, lex_q)
            vec = _vector_ranks(indexed, vector_by_text[vec_q])
            fused = [k for k, _ in rrf_fuse([lex, vec], k=_RRF_K,
                                            weights=[weight, 1.0])]  # fmt: skip
            chunk_hit, pool_hit = pool_hits(fused, md_by_key, section_units)
            counts[name].update(chunk=chunk_hit, pool=pool_hit)
        if i % 25 == 0:
            print(f"ranked {i}/{len(keys)}", flush=True)

    n = len(keys)
    lines = [
        f"# M10a query cleaning, half {args.half} ({n} tasks)",
        "",
        "Answer in the fused top-30 the reranker sees (ranking only, no rerank).",
        "",
        "| variant | one chunk >= 0.5 | pool together >= 0.5 |",
        "|---|---|---|",
    ]
    lines += [
        f"| {name} | {c['chunk']} ({c['chunk'] / n:.3f}) | "
        f"{c['pool']} ({c['pool'] / n:.3f}) |"
        for name, c in counts.items()
    ]
    report = "\n".join(lines) + "\n"
    (_OUT / f"query_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}")


if __name__ == "__main__":
    main()
