"""M10a P3: what expanding a fragment to its whole section could buy (dev only).

Context engineering favors progressive disclosure: fragments first, more only
when the agent asks (docs/CONTEXT_ENGINEERING.md, P3). Pulling neighbors into
every answer was rejected on half B (Phase 3 item 3), because every call paid
for it. Before adding an on-demand "expand to section" option to the contract,
this script measures its ceiling: an agent that expands the best fragment
(highest selection value) of NINA's answer to its section.

Two expansions, both appended after NINA's context:

* ``section``: every chunk of the same document with the same heading path;
* ``subtree``: the same plus the chunks of its subsections.

Each is compared with simply giving NINA the same tokens as budget
(``equal_tokens``: NINA at a per-task budget equal to the expanded context's
size). Expansion is only worth a contract change if it beats that. Rule fixed
before the first run: DECISIONS.md 2026-10-28.

Selection only, from the disk-cached providers, like ``bench.m10a_selection``;
section chunks come from re-chunking the frozen page (no provider call).

Usage:
    uv run python -m bench.m10a_section_expand --half A
    uv run python -m bench.m10a_section_expand --half B --confirm subtree
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.index.chunker import Chunk
from app.index.tokens import count_tokens
from app.retrieve.select import Candidate

#: Budgets the expansion is measured at.
BUDGETS = (2_000, 4_000)
MODES = ("section", "subtree")
#: Rule (DECISIONS 2026-10-28): at 4k on half A, expansion must rescue at least
#: this share of NINA's misses before half B is opened.
MIN_RESCUE = 0.10
_OUT = Path("bench/results/m10a")


def best_candidate(selected: Sequence[Candidate]) -> Candidate | None:
    """The selected candidate with the highest value (ties: document order)."""
    if not selected:
        return None
    return max(selected, key=lambda c: (c.value, -c.ord))


def section_chunks(
    chunks: Sequence[Chunk], heading_path: Sequence[str], mode: str
) -> list[Chunk]:
    """The document's chunks in ``heading_path`` (and below it, for subtree)."""
    path = list(heading_path)
    if mode == "section":
        return [c for c in chunks if c.heading_path == path]
    return [c for c in chunks if c.heading_path[: len(path)] == path]


def expand(
    context: str,
    url: str,
    section: Sequence[Chunk],
    already: set[int],
) -> str:
    """``context`` plus the section chunks it does not hold yet, in page order."""
    extra = [c.md for c in sorted(section, key=lambda c: c.ord) if c.ord not in already]
    if not extra:
        return context
    return context + "\n\n" + "\n\n".join(f"[URL] {url}\n{md}" for md in extra)


def summarize(
    cells: Mapping[str, Mapping[int, Mapping[str, Sequence[Any]]]],
) -> list[str]:
    """Markdown rows: hit, tokens and rescue share per variant and budget."""
    lines = [
        "| variant | budget | hit | mean tokens | misses rescued |",
        "|---|---|---|---|---|",
    ]
    for variant, by_budget in cells.items():
        for budget, cell in by_budget.items():
            base = cells["nina"][budget]["hit"]
            misses = [i for i, h in enumerate(base) if not h]
            rescued = sum(cell["hit"][i] for i in misses)
            share = rescued / len(misses) if misses else 0.0
            lines.append(
                f"| {variant} | {budget} | {statistics.mean(cell['hit']):.3f} "
                f"| {statistics.mean(cell['tokens']):,.0f} "
                f"| {rescued}/{len(misses)} ({share:.3f}) |"
            )
    return lines


def rescue_share(base: Sequence[bool], variant: Sequence[bool]) -> float:
    misses = [i for i, h in enumerate(base) if not h]
    return sum(variant[i] for i in misses) / len(misses) if misses else 0.0


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    import bench.runners.ours as ours_module
    from app.config import get_settings
    from app.index.chunker import chunk_document
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from app.retrieve.select import select
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import bench_task
    from bench.m10a_diagnose import paired
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider
    from bench.runners.ours import OursRunner, _extract_ir, _source_for
    from bench.section_recall import HIT, coverage, units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", choices=list(MODES))
    args = parser.parse_args(argv)

    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CachingRerankProvider(JinaRerankProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    lines_in = (_OUT / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [
        k
        for k in map(json.loads, lines_in)
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    settings = get_settings()
    runner = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=embeddings,
        reranker=reranker,
    )
    chunk_cache: dict[str, list[Chunk]] = {}

    def doc_chunks(url: str) -> list[Chunk]:
        if url not in chunk_cache:
            content_type = store.content_type(url)
            source, _, _ = _source_for(url, content_type)
            doc = _extract_ir(store.raw_bytes(url), content_type, source)
            chunk_cache[url] = chunk_document(doc)
        return chunk_cache[url]

    cells: dict[str, dict[int, dict[str, list[Any]]]] = {}

    def record(variant: str, budget: int, ctx: str, su: Sequence[str]) -> None:
        cell = cells.setdefault(variant, {}).setdefault(
            budget, {"hit": [], "tokens": []}
        )
        cell["hit"].append(coverage(su, ctx) >= HIT)
        cell["tokens"].append(count_tokens(ctx))

    original = ours_module.select
    try:
        for key in keys:
            prep = runner.prepare_candidates(
                bench_task(tasks[key["task_id"]]), store=store
            )
            su = units(key["section"])
            for budget in BUDGETS:
                sink: list[list[Candidate]] = []

                def spy(cands, *, budget_tokens, lambda_, min_value, sink=sink):  # type: ignore[no-untyped-def]
                    out = select(cands, budget_tokens=budget_tokens, lambda_=lambda_,
                                 min_value=min_value)  # fmt: skip
                    sink.append(out)
                    return out

                ours_module.select = spy  # type: ignore[assignment]
                base = runner.assemble_from_candidates(prep, budget_tokens=budget)
                ours_module.select = original
                record("nina", budget, base.text, su)
                selected = sink[0] if sink else []
                best = best_candidate(selected)
                for mode in MODES:
                    ctx = base.text
                    if best is not None:
                        url = prep.url_by_document[best.document_id]
                        chunks = doc_chunks(url)
                        heading = next(
                            c.heading_path for c in chunks if c.ord == best.ord
                        )
                        already = {
                            c.ord for c in selected if c.document_id == best.document_id
                        }
                        ctx = expand(ctx, url, section_chunks(chunks, heading, mode),
                                     already)  # fmt: skip
                    record(mode, budget, ctx, su)
                    equal = runner.assemble_from_candidates(
                        prep, budget_tokens=max(budget, count_tokens(ctx))
                    )
                    record(f"equal_tokens_{mode}", budget, equal.text, su)
    finally:
        ours_module.select = original

    out = [f"# M10a P3: expand to section, half {args.half} ({len(keys)} tasks)", ""]
    out += summarize(cells)
    base4 = cells["nina"][4_000]["hit"]
    best_rescue = max(rescue_share(base4, cells[m][4_000]["hit"]) for m in MODES)
    if best_rescue < MIN_RESCUE:
        out += ["", f"Rescue at 4000 is {best_rescue:.3f} (< {MIN_RESCUE}): "
                "P3 stops here, no contract change."]  # fmt: skip
    name = f"section_expand_{args.half}"
    if args.confirm:
        name = f"confirm_section_{args.confirm}_{args.half}"
        out += ["", f"## Acceptance: {args.confirm} vs equal tokens", ""]
        verdicts = []
        for budget in BUDGETS:
            expanded = cells[args.confirm][budget]["hit"]
            equal = cells[f"equal_tokens_{args.confirm}"][budget]["hit"]
            p = paired(expanded, equal)
            ok = p["lo"] > 0
            verdicts.append(ok)
            mark = "PASS" if ok else "FAIL"
            out.append(f"- {mark} hit vs equal tokens at {budget}: {p['diff']:+.3f} "
                       f"[{p['lo']:+.3f}, {p['hi']:+.3f}]")  # fmt: skip
        out += ["", f"**{'ACCEPTED' if all(verdicts) else 'REJECTED'}**"]
    report = "\n".join(out) + "\n"
    (_OUT / f"{name}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
