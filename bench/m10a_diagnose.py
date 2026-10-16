"""M10a Phase 2: where NINA loses the answer-key section (dev only, no new spend).

For every keyed ``dev`` task the section's coverage (``bench.section_recall``)
is measured at each stage of the ours pipeline, with the frozen settings:

* ``ir``: NINA's IR of the anchored page, rendered to Markdown;
* ``index``: every chunk indexed for the task, together; ``best_chunk``: the
  single chunk holding most of it;
* ``pool``: the candidates the reranker scored, together (and their tokens);
* the context selected at each budget.

A miss at a budget (context coverage below ``HIT``) gets the first cause that
applies: ``extraction_loss`` (not in the IR), ``out_of_pool_split`` (not in
the pool and no single chunk holds it: the section was cut across chunks),
``out_of_pool_ranked`` (one chunk holds it but did not make the pool), or
``selection_drop`` (in the pool, left out of the context).

The paired comparison against strong_rag reads ``baseline_real.csv``
(``bench.m10a_baseline``): hit-rate difference with a paired bootstrap 95%
interval (10,000 resamples, PREREG seed) and an exact McNemar p-value.

Usage (real providers through the disk cache; prints how many paid calls it
made, which should be zero):
    uv run python -m bench.m10a_diagnose
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.extract.to_markdown import to_markdown
from app.index.tokens import count_tokens
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from bench.corpus_store import CorpusStore
from bench.external_tasks import SEED
from bench.m10a_baseline import BUDGETS, bench_task
from bench.runners.ours import OursRunner, _extract_ir, _source_for
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import ExternalTask, load_external_tasks

#: Miss causes, in the order they are tested.
CAUSES = (
    "extraction_loss",
    "out_of_pool_split",
    "out_of_pool_ranked",
    "selection_drop",
)
_OUT = Path("bench/results/m10a")


def cause(trace: Mapping[str, float]) -> str:
    """The first cause that explains a miss (module order)."""
    if trace["ir"] < HIT:
        return "extraction_loss"
    if trace["pool"] < HIT:
        return (
            "out_of_pool_split" if trace["best_chunk"] < HIT else "out_of_pool_ranked"
        )
    return "selection_drop"


def trace_task(
    task: ExternalTask,
    key: Mapping[str, Any],
    store: CorpusStore,
    *,
    embeddings: EmbeddingProvider | None = None,
    reranker: RerankProvider | None = None,
    budgets: Sequence[int] = BUDGETS,
) -> dict[str, Any]:
    """Section coverage at every pipeline stage for one task."""
    section_units = units(key["section"])
    page = key["url"]
    source, _doc_id, _version_id = _source_for(page, store.content_type(page))
    ir = to_markdown(
        _extract_ir(store.raw_bytes(page), store.content_type(page), source)
    )

    settings = get_settings()
    nina = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    query_task = bench_task(task)
    indexed, _urls = nina._index_task(query_task, store)
    chunks = [item.chunk.md for item in indexed]
    prepared = nina.prepare_candidates(query_task, store=store)
    pool = list(prepared.md_by_key.values())

    trace: dict[str, Any] = {
        "task_id": task.id,
        "site": task.site,
        "section_tokens": key["section_tokens"],
        "ir": coverage(section_units, ir),
        "index": coverage(section_units, "\n\n".join(chunks)),
        "best_chunk": max((coverage(section_units, c) for c in chunks), default=0.0),
        "pool": coverage(section_units, "\n\n".join(pool)),
        "pool_n": len(prepared.candidates),
        "pool_tokens": sum(c.tokens for c in prepared.candidates),
        "index_tokens": sum(count_tokens(c) for c in chunks),
    }
    for budget in budgets:
        context = nina.assemble_from_candidates(prepared, budget_tokens=budget).text
        trace[f"ctx_{budget}"] = coverage(section_units, context)
    return trace


def paired(a: Sequence[bool], b: Sequence[bool], seed: int = SEED) -> dict[str, float]:
    """Hit-rate difference a - b, paired bootstrap 95% CI, exact McNemar p."""
    n = len(a)
    diffs = [int(x) - int(y) for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    boots = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(10_000)
    )
    only_a = sum(1 for d in diffs if d == 1)
    only_b = sum(1 for d in diffs if d == -1)
    m = only_a + only_b
    tail = (
        sum(math.comb(m, i) for i in range(min(only_a, only_b) + 1)) / 2**m
        if m
        else 1.0
    )
    return {
        "diff": sum(diffs) / n,
        "lo": boots[249],
        "hi": boots[9_749],
        "only_a": only_a,
        "only_b": only_b,
        "p": min(1.0, 2 * tail),
    }


def _report(
    traces: Sequence[Mapping[str, Any]], baseline: Sequence[Mapping[str, str]]
) -> str:
    lines = ["# M10a Phase 2: where NINA loses the section (dev, 315 tasks)", ""]
    lines += ["## Causes of NINA misses, by budget", "",
              "| budget | misses | " + " | ".join(CAUSES) + " |",
              "|---" * (len(CAUSES) + 2) + "|"]  # fmt: skip
    for budget in BUDGETS:
        misses = [t for t in traces if t[f"ctx_{budget}"] < HIT]
        counts = Counter(cause(t) for t in misses)
        lines.append(
            f"| {budget} | {len(misses)} | "
            + " | ".join(str(counts[c]) for c in CAUSES)
            + " |"
        )

    lines += ["", "## Misses at 4k by site and section size", ""]
    for label, group_of in (
        ("site", lambda t: t["site"]),
        ("section tokens", lambda t: "<=300" if t["section_tokens"] <= 300
         else "301-1500" if t["section_tokens"] <= 1500 else ">1500"),
    ):  # fmt: skip
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for t in traces:
            groups.setdefault(group_of(t), []).append(t)
        lines.append(f"| {label} | n | hit rate at 4k | top cause of misses |")
        lines.append("|---|---|---|---|")
        for name, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            misses = [t for t in group if t["ctx_4000"] < HIT]
            top = Counter(cause(t) for t in misses).most_common(1)
            lines.append(
                f"| {name} | {len(group)} | {1 - len(misses) / len(group):.3f} "
                f"| {top[0][0] + f' ({top[0][1]})' if top else '-'} |"
            )
        lines.append("")

    pool_tokens = sorted(t["pool_tokens"] for t in traces)
    under = sum(1 for p in pool_tokens if p < 8_000)
    lines += [
        "## The candidate-pool hypothesis",
        "",
        f"- Pool tokens (the {traces[0]['pool_n'] if traces else 0}-ish reranked "
        f"candidates): median {pool_tokens[len(pool_tokens) // 2]:,}, "
        f"{under} of {len(traces)} tasks below 8,000.",
        f"- Section anywhere in the index: {sum(t['index'] >= HIT for t in traces)}; "
        f"in the pool: {sum(t['pool'] >= HIT for t in traces)}; "
        f"in one chunk: {sum(t['best_chunk'] >= HIT for t in traces)}.",
        "",
        "## NINA vs strong_rag, paired (hit rate difference, 95% CI, McNemar p)",
        "",
        "| budget | diff | 95% CI | nina only | strong only | p |",
        "|---|---|---|---|---|---|",
    ]
    hits = {
        (r["task_id"], r["system"], r["budget"]): r["hit"] == "True" for r in baseline
    }
    ids = [t["task_id"] for t in traces]
    for budget in BUDGETS:
        s = paired(
            [hits[(i, "nina", str(budget))] for i in ids],
            [hits[(i, "strong_rag", str(budget))] for i in ids],
        )
        lines.append(
            f"| {budget} | {s['diff']:+.3f} | [{s['lo']:+.3f}, {s['hi']:+.3f}] "
            f"| {s['only_a']} | {s['only_b']} | {s['p']:.3f} |"
        )

    lines += ["", "## Examples (3 per cause, at 4k)", ""]
    for name in CAUSES:
        examples = [t for t in traces if t["ctx_4000"] < HIT and cause(t) == name][:3]
        for t in examples:
            lines.append(
                f"- {name}: {t['task_id']} ({t['site']}, {t['section_tokens']} tokens) "
                f"ir={t['ir']:.2f} best_chunk={t['best_chunk']:.2f} "
                f"pool={t['pool']:.2f} ctx4k={t['ctx_4000']:.2f}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:  # pragma: no cover - real corpus and cached providers
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CachingRerankProvider(JinaRerankProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    keys = [
        json.loads(line)
        for line in (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    traces = []
    for i, key in enumerate(keys, 1):
        traces.append(
            trace_task(
                tasks[key["task_id"]],
                key,
                store,
                embeddings=embeddings,
                reranker=reranker,
            )
        )
        if i % 50 == 0:
            print(f"{i}/{len(keys)} tasks", flush=True)
    with (_OUT / "diagnose.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(traces[0]))
        writer.writeheader()
        writer.writerows(traces)
    with (_OUT / "baseline_real.csv").open(encoding="utf-8", newline="") as fh:
        baseline = list(csv.DictReader(fh))
    report = _report(traces, baseline)
    (_OUT / "failures.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
