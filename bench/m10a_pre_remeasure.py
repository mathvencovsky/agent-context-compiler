"""Re-measure the dev tasks whose pages changed with the ``<pre>`` line-break fix.

The fix (DECISIONS.md 2026-10-17) changed the chunk text of 154 corpus pages.
For every keyed dev task that cites one of them, NINA (frozen ``app.config``
defaults) is measured with the extraction before the fix (cache only) and after
it (new embeddings and rerank calls, authorized by the human: ~US$ 0.003 of
embeddings and up to ~0.63M Jina tokens). The paid-call counts are printed.

Usage:
    uv run python -m bench.m10a_pre_remeasure
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

import app.extract.html as html_module
from app.config import Settings
from app.index.chunker import chunk_document
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_diagnose import _extract_ir, _source_for, paired
from bench.runners.ours import OursRunner
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

_OUT = Path("bench/results/m10a")


def changed_pages(store: CorpusStore, old: Callable[[Any], str]) -> set[str]:
    """Final URLs of the corpus pages whose chunk texts differ after the fix."""
    new = html_module._raw_code
    pages = set()
    for entry in store.entries():
        content_type = store.content_type(entry.url) if entry.status == "ok" else ""
        if "html" not in content_type:
            continue
        raw = store.raw_bytes(entry.url)
        source, _, _ = _source_for(entry.url, content_type)
        try:
            html_module._raw_code = old
            before = [
                c.text for c in chunk_document(_extract_ir(raw, content_type, source))
            ]
        finally:
            html_module._raw_code = new
        after = [c.text for c in chunk_document(_extract_ir(raw, content_type, source))]
        if before != after:
            pages.add(entry.final_url)
    return pages


def main() -> None:  # pragma: no cover - real corpus and providers
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CachingRerankProvider(JinaRerankProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    old: Callable[[Any], str] = lambda el: el.text_content() or ""  # noqa: E731
    pages = changed_pages(store, old)
    keys = [
        k
        for k in map(
            json.loads, (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
        )
        if set(tasks[k["task_id"]].urls) & pages
    ]
    print(f"{len(pages)} changed pages; {len(keys)} keyed dev tasks cite them")
    frozen = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = OursRunner(
        lambda_=frozen.selection_lambda,
        min_value=frozen.min_value,
        lexical_weight=frozen.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    rows: dict[str, dict[int, list[float]]] = {"before": {}, "after": {}}
    new = html_module._raw_code
    for name, raw_code in (("before", old), ("after", new)):
        html_module._raw_code = raw_code
        try:
            for key in keys:
                prep = runner.prepare_candidates(
                    bench_task(tasks[key["task_id"]]), store=store
                )
                for budget in BUDGETS:
                    text = runner.assemble_from_candidates(
                        prep, budget_tokens=budget
                    ).text
                    rows[name].setdefault(budget, []).append(
                        coverage(units(key["section"]), text)
                    )
        finally:
            html_module._raw_code = new
        print(f"{name}: paid calls so far: embeddings {embeddings.api_calls}, "
              f"rerank {reranker.api_calls}", flush=True)  # fmt: skip

    halves = ["A" if in_tune_half(k["task_id"]) else "B" for k in keys]
    lines = [
        f"# `<pre>` fix: re-measured dev tasks ({len(keys)} tasks; "
        f"{halves.count('A')} in half A, {halves.count('B')} in half B)",
        "",
        "| budget | coverage before | coverage after | hit before | hit after | "
        "hit diff [95% CI] |",
        "|---|---|---|---|---|---|",
    ]
    for budget in BUDGETS:
        before, after = rows["before"][budget], rows["after"][budget]
        p = paired([c >= HIT for c in after], [c >= HIT for c in before])
        lines.append(
            f"| {budget} | {statistics.mean(before):.3f} | {statistics.mean(after):.3f}"
            f" | {statistics.mean(c >= HIT for c in before):.3f}"
            f" | {statistics.mean(c >= HIT for c in after):.3f}"
            f" | {p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}] |"
        )
    report = "\n".join(lines) + "\n"
    (_OUT / "pre_remeasure.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
