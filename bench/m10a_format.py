"""Tokens the agent receives from ``assemble``: detailed JSON vs concise Markdown.

Offline (frozen corpus, disk-cached providers, no new spend expected; the
paid-call counts are printed). For every keyed dev task and budget it selects the
fragments with the frozen ``app.config`` defaults, builds the real
:class:`~app.retrieve.assemble.AssembleResult` (evidence from the app's own
``_best_quote``), and counts the tokens of:

* ``detailed_mcp``: the detailed body as the MCP SDK sent it as text (JSON,
  2-space indent); before 2026-10-16 the same body also went as
  ``structuredContent``;
* ``detailed_compact``: the same body without indentation;
* ``concise``: :func:`app.retrieve.render.render_concise`;
* ``md_only``: the fragments' Markdown alone (the floor).

The echoed query is the question title, as short as an agent's query. It
asserts that the concise text holds every fragment's Markdown verbatim and
reports where its section coverage differs from ``md_only``.

Usage:
    uv run python -m bench.m10a_format
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from app.api.assemble import _to_response
from app.config import Settings
from app.index.tokens import count_tokens
from app.retrieve.assemble import (
    AssembleResult,
    Fragment,
    FragmentScores,
    SourceStatus,
    _best_quote,
)
from app.retrieve.render import render_concise
from app.retrieve.select import select
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_diagnose import _extract_ir, _source_for
from bench.runners.ours import OursRunner
from bench.section_recall import coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

_OUT = Path("bench/results/m10a")
FORMATS = ("detailed_mcp", "detailed_compact", "concise", "md_only")


def main() -> None:  # pragma: no cover - real corpus
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
    frozen = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = OursRunner(
        lambda_=frozen.selection_lambda,
        min_value=frozen.min_value,
        lexical_weight=frozen.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    tokens: dict[tuple[str, int, str], list[int]] = {}
    diffs: list[float] = []
    for n, key in enumerate(keys, 1):
        ext = tasks[key["task_id"]]
        task = bench_task(ext)
        prep = runner.prepare_candidates(task, store=store)
        indexed, _ = runner._index_task(task, store)
        chunk_by_key = {item.key: item.chunk for item in indexed}
        ir_by_version = {}
        for url in task.urls:
            try:
                source, _, _ = _source_for(url, store.content_type(url))
                doc = _extract_ir(store.raw_bytes(url), store.content_type(url), source)
            except Exception:  # noqa: BLE001, S112 — a missing page has no chunks
                continue
            ir_by_version[doc.version_id] = doc
        section_units = units(key["section"])
        half = "A" if in_tune_half(key["task_id"]) else "B"
        for budget in BUDGETS:
            selected = select(prep.candidates, budget_tokens=budget,
                              lambda_=frozen.selection_lambda,
                              min_value=frozen.min_value)  # fmt: skip
            fragments = []
            for i, c in enumerate(selected):
                chunk = chunk_by_key[(c.document_id, c.ord)]
                fragments.append(
                    Fragment(
                        chunk_id=str(1_000_000 + i),
                        document_id=c.document_id,
                        version_id=chunk.version_id,
                        source_url=prep.url_by_document.get(c.document_id, ""),
                        heading_path=list(chunk.heading_path),
                        md=chunk.md,
                        tokens=chunk.tokens,
                        scores=FragmentScores(
                            lexical_rank=i + 1,
                            vector_rank=i + 3,
                            rrf=0.0312345678901,
                            rerank=c.value,
                        ),  # fmt: skip
                        evidence=[
                            _best_quote(chunk, ir_by_version, ext.question_title)  # type: ignore[arg-type]
                        ],
                    )
                )
            result = AssembleResult(
                query=ext.question_title,
                fragments=fragments,
                sources=[
                    SourceStatus(url, None, None, "2026-10-16T12:00:00+00:00", "ok")
                    for url in task.urls
                ],
                output_tokens=sum(f.tokens for f in fragments),
                budget_tokens=budget,
                coverage="partial",
            )
            body = _to_response(result).model_dump(mode="json")
            md_only = "\n\n".join(f.md for f in fragments)
            concise = render_concise(result)
            assert all(f.md in concise for f in fragments)
            delta = coverage(section_units, concise) - coverage(section_units, md_only)
            if delta:
                diffs.append(delta)
            texts = {
                "detailed_mcp": json.dumps(body, indent=2, ensure_ascii=False),
                "detailed_compact": json.dumps(body, ensure_ascii=False),
                "concise": concise,
                "md_only": md_only,
            }
            for name, text in texts.items():
                for group in ("all", half):
                    tokens.setdefault((group, budget, name), []).append(
                        count_tokens(text)
                    )
        if n % 50 == 0:
            print(f"{n}/{len(keys)}", flush=True)

    lines = [
        f"# M10a response formats ({len(keys)} keyed dev tasks)",
        "",
        "Mean tokens the agent receives per `assemble` call; in parentheses, the "
        "change against `detailed_mcp`. Concise holds every fragment verbatim "
        "(asserted).",
        "",
    ]
    for group in ("all", "A", "B"):
        lines += [f"## {group}", "", "| budget | " + " | ".join(FORMATS) + " |",
                  "|---" * (1 + len(FORMATS)) + "|"]  # fmt: skip
        for budget in BUDGETS:
            base = statistics.mean(tokens[(group, budget, "detailed_mcp")])
            cells = []
            for name in FORMATS:
                m = statistics.mean(tokens[(group, budget, name)])
                cells.append(f"{m:,.0f} ({m / base - 1:+.0%})")
            lines.append(f"| {budget} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += [
        f"Section coverage, concise minus md_only: {len(diffs)} of "
        f"{len(keys) * len(BUDGETS)} task-budgets differ "
        f"({sum(d < 0 for d in diffs)} lower, {sum(d > 0 for d in diffs)} higher). "
        "Higher means a fragment header (its heading path) matched a heading "
        "unit of the section; no fragment text is lost.",
        "",
    ]
    report = "\n".join(lines)
    (_OUT / "format.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
