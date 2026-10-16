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
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.retrieve.rrf import rrf_fuse
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import bench_task
from bench.m10a_pool import pool_hits
from bench.runners.ours import _RRF_K, OursRunner, _lexical_ranks, _vector_ranks
from bench.section_recall import units
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


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from bench.provider_cache import CachingEmbeddingProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument(
        "--data-root", type=Path, default=Path("."),
        help="checkout holding bench/corpus, bench/.cache and the qrels",
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
    lines: list[Any] = [
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
