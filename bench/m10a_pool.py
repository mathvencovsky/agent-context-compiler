"""M10a Phase 3, item 2: why the answer chunk misses the rerank pool.

The runner's lexical ranking counts every occurrence of any question word, so on
long questions common words and long chunks win and RRF drops chunks only the
vector search found. This module ranks the pool with lexical variants (BM25,
title-only query, lower lexical weight) and counts how often the answer reaches
the top-30 the reranker sees. Ranking only: no rerank calls; the query
embedding comes from the disk cache (paid-call counts are printed).

Usage:
    uv run python -m bench.m10a_pool --half A
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.retrieve.rrf import rrf_fuse
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import bench_task
from bench.runners.ours import (
    _RRF_K,
    _SEARCH_TOP_K,
    OursRunner,
    _IndexedChunk,
    _Key,
    _lexical_ranks,
    _tokenize,
    _vector_ranks,
)
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: The fused prefix the reranker sees (``app.retrieve.rerank``).
POOL = 30
_OUT = Path("bench/results/m10a")


def bm25_ranks(
    indexed: Sequence[_IndexedChunk], query: str, k1: float = 1.2, b: float = 0.75
) -> list[_Key]:
    """Okapi BM25 over the task's chunks (top-50), textbook ``k1`` and ``b``.

    Rare words weigh more than common ones and long chunks are normalized by
    length; each distinct query word counts once. Ties break on the key.
    """
    docs = [(item.key, Counter(_tokenize(item.chunk.text))) for item in indexed]
    if not docs:
        return []
    n = len(docs)
    avgdl = sum(sum(tf.values()) for _, tf in docs) / n or 1.0
    terms = set(_tokenize(query))
    df = Counter(t for _, tf in docs for t in terms if t in tf)
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
    scored = []
    for key, tf in docs:
        dl = sum(tf.values())
        score = sum(
            idf[t] * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * dl / avgdl))
            for t in idf
            if t in tf
        )
        if score > 0:
            scored.append((score, key))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [key for _, key in scored[:_SEARCH_TOP_K]]


Lexical = Callable[[list[_IndexedChunk], str], list[_Key]]
#: name -> (lexical ranking, query it gets: "full" or "title", lexical weight)
VARIANTS: dict[str, tuple[Lexical, str, float]] = {
    "current": (_lexical_ranks, "full", 1.0),
    "bm25": (bm25_ranks, "full", 1.0),
    "lexw_0.5": (_lexical_ranks, "full", 0.5),
    "title": (_lexical_ranks, "title", 1.0),
    "bm25_title": (bm25_ranks, "title", 1.0),
    "lexw_0.25": (_lexical_ranks, "full", 0.25),
    "bm25_lexw_0.5": (bm25_ranks, "full", 0.5),
    "vector_only": (_lexical_ranks, "full", 0.0),
}


def pool_hits(
    fused: Sequence[_Key], md_by_key: dict[_Key, str], section_units: Sequence[str]
) -> tuple[bool, bool]:
    """(one pool chunk holds half the section, the pool together does)."""
    pool = [md_by_key[k] for k in fused[:POOL]]
    best = max((coverage(section_units, md) for md in pool), default=0.0)
    return best >= HIT, coverage(section_units, "\n\n".join(pool)) >= HIT


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from bench.provider_cache import CachingEmbeddingProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    args = parser.parse_args(argv)

    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    keys = [
        k
        for k in map(
            json.loads, (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    runner = OursRunner(embeddings=embeddings)
    weight = get_settings().lexical_weight
    counts: dict[str, Counter[str]] = {name: Counter() for name in VARIANTS}
    for i, key in enumerate(keys, 1):
        task = bench_task(tasks[key["task_id"]])
        indexed, _ = runner._index_task(task, store)
        md_by_key = {item.key: item.chunk.md for item in indexed}
        vector = _vector_ranks(indexed, embeddings.embed([task.question])[0])
        section_units = units(key["section"])
        queries = {"full": task.question, "title": tasks[key["task_id"]].question_title}
        for name, (lexical, query, w) in VARIANTS.items():
            lex = lexical(indexed, queries[query])
            fused = [k for k, _ in rrf_fuse([lex, vector], k=_RRF_K,
                                            weights=[w * weight, 1.0])]  # fmt: skip
            chunk_hit, pool_hit = pool_hits(fused, md_by_key, section_units)
            counts[name].update(chunk=chunk_hit, pool=pool_hit)
        if i % 25 == 0:
            print(f"ranked {i}/{len(keys)}", flush=True)

    n = len(keys)
    lines: list[Any] = [
        f"# M10a pool variants, half {args.half} ({n} tasks)",
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
    (_OUT / f"pool_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}")


if __name__ == "__main__":
    main()
