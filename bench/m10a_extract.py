"""M10a Phase 3, item 4: pages with no main container lose text in extraction.

Compares the extractor without and with the dominant-container fallback
(``app.extract.html``): ``current`` sets ``_READABILITY_MIN_SHARE`` to 0, so the
new fallback never fires. Measures the section's coverage in the extraction and
the full pipeline (frozen ``app.config`` defaults, not the local ``.env``) at
every budget. Re-extracted pages need new embeddings and rerank calls; the
counts are printed.

Usage:
    uv run python -m bench.m10a_extract --half A
    uv run python -m bench.m10a_extract --half B --confirm
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

import app.extract.html as html_module
from app.config import Settings
from app.extract.to_markdown import to_markdown
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_diagnose import _extract_ir, _source_for
from bench.m10a_selection import _strong_rag, per_1k
from bench.runners.ours import OursRunner
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: name -> ``_READABILITY_MIN_SHARE``
VARIANTS = {"current": 0.0, "container_fallback": 0.8}
_OUT = Path("bench/results/m10a")


def confirm_extraction(
    candidate: Mapping[int, Mapping[str, Sequence[Any]]],
    current: Mapping[int, Mapping[str, Sequence[Any]]],
    strong: Mapping[int, Mapping[str, Sequence[float]]],
    ir: Mapping[str, Sequence[float]],
    sites: Sequence[str],
    target: int = 8_000,
) -> list[tuple[str, bool, str]]:
    """The item 4 acceptance rule (DECISIONS 2026-10-16), one row per check.

    ``ir`` maps each variant name to the per-task section coverage of the
    extraction; ``sites`` gives each task's site, in the same order.
    """
    new, old = ir["container_fallback"], ir["current"]
    worse = sum(n < o for n, o in zip(new, old, strict=True))
    pg = [i for i, s in enumerate(sites) if s == "postgresql"]
    pg_new = statistics.mean(new[i] for i in pg) if pg else 0.0
    pg_old = statistics.mean(old[i] for i in pg) if pg else 0.0
    checks = [
        ("extraction coverage: no task drops", worse == 0, f"{worse} drop"),
        (f"extraction coverage: PostgreSQL mean rises ({len(pg)} tasks)",
         pg_new > pg_old, f"{pg_old:.3f} -> {pg_new:.3f}"),
    ]  # fmt: skip
    for budget in candidate:
        drop = statistics.mean(candidate[budget]["hit"]) - statistics.mean(
            current[budget]["hit"]
        )
        checks.append((f"hit at {budget}: no drop over 2 pts", drop >= -0.02,
                       f"{drop:+.3f}"))  # fmt: skip
    for budget in candidate:
        ours, theirs = per_1k(candidate[budget]), per_1k(strong[budget])
        checks.append((f"coverage per 1k tokens at {budget} > strong_rag",
                       ours > theirs, f"{ours:.3f} vs {theirs:.3f}"))  # fmt: skip
    a = statistics.median(candidate[target]["ms"])
    b = statistics.median(current[target]["ms"])
    checks.append((f"median ms at {target}: at most +50%", a <= 1.5 * b,
                   f"{a:.0f} vs {b:.0f}"))  # fmt: skip
    return checks


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", action="store_true")
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
    frozen = Settings(_env_file=None)  # type: ignore[call-arg]
    runner = OursRunner(
        lambda_=frozen.selection_lambda,
        min_value=frozen.min_value,
        lexical_weight=frozen.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    lines = [
        f"# M10a extraction fallback, half {args.half} ({len(keys)} tasks)",
        "",
        "| variant | extraction hit | "
        + " | ".join(f"hit {b} | tokens {b}" for b in BUDGETS)
        + " |",
        "|---" * (2 + 2 * len(BUDGETS)) + "|",
    ]
    original = html_module._READABILITY_MIN_SHARE
    measured: dict[str, dict[int, dict[str, list[Any]]]] = {}
    ir: dict[str, list[float]] = {}
    try:
        for name, share in VARIANTS.items():
            html_module._READABILITY_MIN_SHARE = share
            cells: dict[int, dict[str, list[Any]]] = {
                b: {"hit": [], "coverage": [], "tokens": [], "ms": []} for b in BUDGETS
            }
            ir[name] = []
            for i, key in enumerate(keys, 1):
                section_units = units(key["section"])
                page = key["url"]
                source, _, _ = _source_for(page, store.content_type(page))
                ir_md = to_markdown(
                    _extract_ir(store.raw_bytes(page), store.content_type(page), source)
                )
                ir[name].append(coverage(section_units, ir_md))
                for attempt in range(6):
                    start = perf_counter()
                    try:
                        prep = runner.prepare_candidates(
                            bench_task(tasks[key["task_id"]]), store=store
                        )
                        break
                    except httpx.HTTPStatusError as error:
                        # shortcut: bench-only backoff on Jina 429 (rate limit);
                        # move it into JinaRerankProvider if the product hits it.
                        if error.response.status_code != 429 or attempt == 5:
                            raise
                        print(f"429 from the reranker; waiting {30 * (attempt + 1)}s")
                        time.sleep(30 * (attempt + 1))
                prepare_ms = (perf_counter() - start) * 1000
                for budget in BUDGETS:
                    start = perf_counter()
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    cov = coverage(section_units, result.text)
                    cell = cells[budget]
                    cell["ms"].append(prepare_ms + (perf_counter() - start) * 1000)
                    cell["hit"].append(cov >= HIT)
                    cell["coverage"].append(cov)
                    cell["tokens"].append(result.context_tokens)
                if i % 25 == 0:
                    print(f"{name} {i}/{len(keys)} | paid calls: embeddings "
                          f"{embeddings.api_calls}, rerank {reranker.api_calls}",
                          flush=True)  # fmt: skip
            measured[name] = cells
            row = [f"{statistics.mean(c >= HIT for c in ir[name]):.3f}"]
            row += [
                f"{statistics.mean(cells[b]['hit']):.3f} | "
                f"{statistics.mean(cells[b]['tokens']):,.0f}"
                for b in BUDGETS
            ]
            lines.append(f"| {name} | " + " | ".join(row) + " |")
    finally:
        html_module._READABILITY_MIN_SHARE = original

    out = f"extract_{args.half}"
    if args.confirm:
        out = f"confirm_container_fallback_{args.half}"
        strong = _strong_rag([key["task_id"] for key in keys])
        checks = confirm_extraction(
            measured["container_fallback"],
            measured["current"],
            strong,
            ir,
            [key["site"] for key in keys],
        )
        lines += ["", "## Acceptance: container_fallback vs current", ""]
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
