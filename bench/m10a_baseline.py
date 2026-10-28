"""M10a baseline: how much of the answer-key section each system delivers (dev only).

For every ``dev`` task with a key (``bench/anchor_qrels.py``), each system gets
the same query (the question's title and body) and the task's five URLs:

* ``full_page``: every URL's ``page_markdown`` (what an agent reading all of
  them gets), unbounded;
* ``naive_rag``: BM25 over fixed chunks (the M8 baseline);
* ``strong_rag``: overlap chunks, hybrid RRF and cross-encoder rerank (M8);
* ``nina``: the ours pipeline with the frozen selection settings of
  ``app/config.py``, candidates prepared once and selected per budget.

Extraction recall is measured on the anchored page alone: ``extract_nina`` is
NINA's IR rendered to Markdown from the raw HTML (what the ours pipeline
indexes), ``extract_trafilatura`` is ``page_markdown``.

Each row records the context's tokens, the section coverage, whether it is a
hit, the first ``[URL]`` fragment holding the section, and the wall time to
build it. Default providers are the deterministic fakes (offline). ``--real``
uses the disk-cached OpenAI embeddings and Jina reranker and costs money.

Usage:
    uv run python -m bench.m10a_baseline [--real] [--limit N]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import get_settings
from app.extract.to_markdown import to_markdown
from app.index.tokens import count_tokens
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.rerank.base import RerankProvider
from bench.corpus_store import CorpusStore
from bench.page_length import page_markdown
from bench.runners.naive_rag import NaiveRagRunner
from bench.runners.ours import OursRunner, _extract_ir, _source_for
from bench.runners.strong_rag import StrongRagRunner
from bench.section_recall import HIT, coverage, first_hit, token_precision, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import ExternalTask, Task, load_external_tasks

#: Context budgets (tokens) the bounded systems are measured at.
BUDGETS = (1_000, 2_000, 4_000, 8_000)
#: Systems reported, in table order.
SYSTEMS = (
    "nina",
    "strong_rag",
    "naive_rag",
    "full_page",
    "extract_nina",
    "extract_trafilatura",
)
_OUT = Path("bench/results/m10a")


@dataclass
class Row:
    """One system's context for one task at one budget (0 = unbounded)."""

    task_id: str
    site: str
    system: str
    budget: int
    tokens: int
    coverage: float
    hit: bool
    first_hit: int | None
    ms: float
    precision: float = 0.0  # share of the context's tokens that are the section
    iou: float = 0.0  # token-level intersection over union with the section


def bench_task(task: ExternalTask) -> Task:
    """The M8 runner input: the question's title and body over the task's URLs."""
    return Task(
        id=task.id,
        category="guide",
        split="dev",
        urls=task.urls,
        question=f"{task.question_title}\n\n{task.question}",
        answer_type="judge",
        expected=[""],
        expected_evidence=[],
        author="human",
        validated=True,
    )


def _row(task: ExternalTask, system: str, budget: int, ctx: str, ms: float,
         section_units: Sequence[str], section: str) -> Row:  # fmt: skip
    cov = coverage(section_units, ctx)
    tp = token_precision(section, ctx)
    return Row(
        task_id=task.id,
        site=task.site,
        system=system,
        budget=budget,
        tokens=count_tokens(ctx),
        coverage=cov,
        hit=cov >= HIT,
        first_hit=first_hit(section_units, ctx),
        ms=ms,
        precision=tp.precision,
        iou=tp.iou,
    )


def measure(
    task: ExternalTask,
    key: Mapping[str, Any],
    store: CorpusStore,
    *,
    embeddings: EmbeddingProvider | None = None,
    reranker: RerankProvider | None = None,
    budgets: Sequence[int] = BUDGETS,
) -> list[Row]:
    """Every system's rows for one task (``None`` providers mean the fakes)."""
    sec = key["section"]
    section_units = units(sec)
    query_task = bench_task(task)
    rows = []

    start = perf_counter()
    ctx = "\n\n".join(f"[URL] {u}\n{page_markdown(store, u)}" for u in task.urls)
    rows.append(_row(task, "full_page", 0, ctx, _ms(start), section_units, sec))

    page = key["url"]
    start = perf_counter()
    source, _doc_id, _version_id = _source_for(page, store.content_type(page))
    ir = to_markdown(
        _extract_ir(store.raw_bytes(page), store.content_type(page), source)
    )
    rows.append(_row(task, "extract_nina", 0, ir, _ms(start), section_units, sec))
    start = perf_counter()
    md = page_markdown(store, page)
    rows.append(
        _row(task, "extract_trafilatura", 0, md, _ms(start), section_units, sec)
    )

    naive = NaiveRagRunner()
    strong = StrongRagRunner(embeddings=embeddings, reranker=reranker)
    for name, runner in (("naive_rag", naive), ("strong_rag", strong)):
        for budget in budgets:
            start = perf_counter()
            result = runner.build_context(query_task, budget_tokens=budget, store=store)
            rows.append(
                _row(task, name, budget, result.text, _ms(start), section_units, sec)
            )

    settings = get_settings()
    nina = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    start = perf_counter()
    prepared = nina.prepare_candidates(query_task, store=store)
    prepare_ms = _ms(start)
    for budget in budgets:
        start = perf_counter()
        result = nina.assemble_from_candidates(prepared, budget_tokens=budget)
        ms = prepare_ms + _ms(start)  # time to the answer at this budget
        rows.append(_row(task, "nina", budget, result.text, ms, section_units, sec))
    return rows


def _ms(start: float) -> float:
    return (perf_counter() - start) * 1000


def summarize(rows: Iterable[Row]) -> str:
    """Markdown table: per system and budget, coverage, hits, tokens and time."""
    groups: dict[tuple[str, int], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.system, row.budget), []).append(row)
    order = {name: i for i, name in enumerate(SYSTEMS)}
    lines = [
        "| system | budget | n | hit rate | mean coverage | mean tokens "
        "| coverage per 1k tokens | token precision | token IoU | median ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (system, budget), group in sorted(
        groups.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])
    ):
        cov = statistics.mean(r.coverage for r in group)
        tokens = statistics.mean(r.tokens for r in group)
        per_1k = cov / tokens * 1000 if tokens else 0.0
        lines.append(
            f"| {system} | {budget or 'page'} | {len(group)} "
            f"| {sum(r.hit for r in group) / len(group):.3f} | {cov:.3f} "
            f"| {tokens:,.0f} | {per_1k:.3f} "
            f"| {statistics.mean(r.precision for r in group):.3f} "
            f"| {statistics.mean(r.iou for r in group):.3f} "
            f"| {statistics.median(r.ms for r in group):,.0f} |"
        )
    return "\n".join(lines) + "\n"


def write_rows(path: Path, rows: Sequence[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(Row)])
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--real", action="store_true", help="real providers (paid)")
    parser.add_argument("--limit", type=int, help="first N keyed tasks only")
    args = parser.parse_args(argv)

    embeddings: EmbeddingProvider | None = None
    reranker: RerankProvider | None = None
    if args.real:
        from app.providers.embeddings.openai import OpenAIEmbeddingProvider
        from app.providers.rerank.jina import JinaRerankProvider
        from bench.provider_cache import (
            CachingEmbeddingProvider,
            CachingRerankProvider,
        )

        embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
        reranker = CachingRerankProvider(JinaRerankProvider())

    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    lines = (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [json.loads(line) for line in lines][: args.limit]
    rows: list[Row] = []
    for i, key in enumerate(keys, 1):
        rows += measure(
            tasks[key["task_id"]], key, store, embeddings=embeddings, reranker=reranker
        )
        if i % 25 == 0:
            print(f"{i}/{len(keys)} tasks", flush=True)
    label = "real" if args.real else "fake"
    write_rows(_OUT / f"baseline_{label}.csv", rows)
    table = summarize(rows)
    (_OUT / f"baseline_{label}.md").write_text(
        f"# M10a baseline ({label} providers, {len(keys)} dev tasks)\n\n{table}",
        encoding="utf-8",
    )
    print(table)


if __name__ == "__main__":
    main()
