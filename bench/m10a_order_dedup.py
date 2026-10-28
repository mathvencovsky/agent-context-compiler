"""M10a research items 4 and 5: document order and near-duplicate fragments.

Item 4, order. Models use what sits in the middle of a long context worst
("Lost in the Middle", Liu et al., TACL 2024). NINA emits the selected
fragments by ``(document_id, ord)``, so with several URLs the order of the
documents is arbitrary. The ``order`` variant keeps the same fragments and
puts the documents in order of their best fragment's value, each document still
read top to bottom (within a document, page order beats score order: OP-RAG,
NVIDIA 2024). The ``edges`` variant puts the best document first and the
second best last, since models read both ends best (Liu et al.; Jin et al.,
"Long-Context LLMs Meet RAG", 2024; Anthropic's long-context study found Claude
best near the end). Coverage cannot change; what moves is where the answer
sits: :func:`at_edge` is whether a fragment that carries the answer section is
the first or the last one.

Item 5, near duplicates. Similar text hurts answers more than unrelated text
("Context Rot", Chroma, 2025), and λ = 0 turned off the only defense against it
(the diversity penalty). Two fragments are near duplicates when the Jaccard
similarity of their word 5-gram sets is at least :data:`NEAR_DUP`. The
script first counts how often the current selection holds such a pair; the
``dedup`` variant then drops, at each greedy step, every remaining candidate
that is a near duplicate of the one just chosen, so the budget goes to new text.

Both are selection-only: candidates are prepared once per task through the
disk-cached providers (no new spend expected; paid-call counts are printed),
exactly like ``bench.m10a_selection``. Acceptance rules: DECISIONS.md
2026-10-28, fixed before this script first ran.

Usage:
    uv run python -m bench.m10a_order_dedup --half A
    uv run python -m bench.m10a_order_dedup --half B --confirm edges
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import bench.runners.ours as ours_module
from app.retrieve.select import Candidate, _adjusted_value, select
from bench.section_recall import HIT, coverage, first_hit, normalize, token_precision

#: Word n-gram size and Jaccard threshold for a near duplicate (fixed now).
SHINGLE = 5
NEAR_DUP = 0.8
#: Item 5 is dropped without the variant if fewer selections hold a pair.
MIN_DUP_SHARE = 0.05
VARIANTS = ("current", "order", "edges", "dedup")
_OUT = Path("bench/results/m10a")
_WORD = re.compile(r"\w+")


def shingles(text: str, n: int = SHINGLE) -> frozenset[tuple[str, ...]]:
    """Word ``n``-grams of the normalized text (the words, for shorter text)."""
    words = _WORD.findall(normalize(text))
    if len(words) < n:
        return frozenset({tuple(words)}) if words else frozenset()
    return frozenset(tuple(words[i : i + n]) for i in range(len(words) - n + 1))


def jaccard(a: frozenset[Any], b: frozenset[Any]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def near_dup_tokens(
    selected: Sequence[Candidate], text_of: Callable[[Candidate], str]
) -> int:
    """Tokens of selected fragments that nearly repeat an earlier one."""
    kept: list[frozenset[tuple[str, ...]]] = []
    wasted = 0
    for c in selected:
        sh = shingles(text_of(c))
        if any(jaccard(sh, k) >= NEAR_DUP for k in kept):
            wasted += c.tokens
        else:
            kept.append(sh)
    return wasted


def select_dedup(
    candidates: Sequence[Candidate],
    *,
    budget_tokens: int,
    lambda_: float,
    min_value: float,
    text_of: Callable[[Candidate], str],
) -> list[Candidate]:
    """``app.retrieve.select.select`` that never keeps two near duplicates.

    The same greedy loop; after each pick, every remaining candidate that is a
    near duplicate of the pick leaves the pool.
    """
    remaining = list(candidates)
    picked: list[Candidate] = []
    budget_left = budget_tokens
    while remaining:
        fitting = [c for c in remaining if 0 < c.tokens <= budget_left]
        if not fitting:
            break
        best = max(fitting, key=lambda c: _adjusted_value(c, picked, lambda_))
        if _adjusted_value(best, picked, lambda_) < min_value:
            break
        picked.append(best)
        budget_left -= best.tokens
        sh = shingles(text_of(best))
        remaining = [
            c
            for c in remaining
            if c is not best and jaccard(shingles(text_of(c)), sh) < NEAR_DUP
        ]
    picked.sort(key=lambda c: (c.document_id, c.ord))
    return picked


def order_by_document_value(selected: Sequence[Candidate]) -> list[Candidate]:
    """Documents by their best fragment's value, each read in page order."""
    best: dict[str, float] = {}
    for c in selected:
        best[c.document_id] = max(best.get(c.document_id, c.value), c.value)
    return sorted(selected, key=lambda c: (-best[c.document_id], c.document_id, c.ord))


def edges_by_document_value(selected: Sequence[Candidate]) -> list[Candidate]:
    """Best document first, second best last, the rest between, in page order."""
    ordered = order_by_document_value(selected)
    docs = list(dict.fromkeys(c.document_id for c in ordered))
    if len(docs) < 3:
        return ordered
    rank = docs[:1] + docs[2:] + docs[1:2]
    pos = {d: i for i, d in enumerate(rank)}
    return sorted(ordered, key=lambda c: (pos[c.document_id], c.ord))


def at_edge(section_units: Sequence[str], context: str) -> bool:
    """Whether the first or the last ``[URL]`` fragment holds a section unit."""
    fragments = [f for f in context.split("[URL] ") if f.strip()]
    if not fragments:
        return False
    ends = {0, len(fragments) - 1}
    return any(unit in normalize(fragments[i]) for i in ends for unit in section_units)


def variant_select(
    variant: str,
    text_of: Callable[[Candidate], str],
    sink: list[list[Candidate]],
) -> Callable[..., list[Candidate]]:
    """A drop-in for ``select`` that runs ``variant`` and records its picks."""

    def patched(
        candidates: list[Candidate],
        *,
        budget_tokens: int,
        lambda_: float,
        min_value: float,
    ) -> list[Candidate]:
        if variant == "dedup":
            out = select_dedup(candidates, budget_tokens=budget_tokens,
                               lambda_=lambda_, min_value=min_value,
                               text_of=text_of)  # fmt: skip
        else:
            out = select(candidates, budget_tokens=budget_tokens, lambda_=lambda_,
                         min_value=min_value)  # fmt: skip
        if variant == "order":
            out = order_by_document_value(out)
        elif variant == "edges":
            out = edges_by_document_value(out)
        sink.append(out)
        return out

    return patched


def measure_cell(
    contexts: Sequence[str], sections: Sequence[str], section_units: Sequence[Any]
) -> dict[str, list[Any]]:
    """Per-task coverage, hit, first_hit and token precision for one variant."""
    cell: dict[str, list[Any]] = {"coverage": [], "hit": [], "first_hit": [],
                                  "edge": [], "precision": []}  # fmt: skip
    for ctx, section, su in zip(contexts, sections, section_units, strict=True):
        cov = coverage(su, ctx)
        cell["coverage"].append(cov)
        cell["hit"].append(cov >= HIT)
        cell["first_hit"].append(first_hit(su, ctx))
        cell["edge"].append(at_edge(su, ctx))
        cell["precision"].append(token_precision(section, ctx).precision)
    return cell


def confirm_order(
    candidate: Mapping[int, Mapping[str, Sequence[Any]]],
    current: Mapping[int, Mapping[str, Sequence[Any]]],
) -> list[tuple[str, bool, str]]:
    """DECISIONS 2026-10-28, item 4: the answer moves to an end, nothing lost."""
    from bench.m10a_diagnose import paired

    checks = []
    for budget in candidate:
        both = [
            i
            for i, (a, b) in enumerate(
                zip(candidate[budget]["hit"], current[budget]["hit"], strict=True)
            )
            if a and b
        ]
        new = [candidate[budget]["edge"][i] for i in both]
        old = [current[budget]["edge"][i] for i in both]
        p = paired(new, old) if both else {"diff": 0.0, "lo": 0.0, "hi": 0.0}
        checks.append((f"answer at an end at {budget}: CI lower bound > 0",
                       p["lo"] > 0,
                       f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}] "
                       f"on {len(both)} hits"))  # fmt: skip
        drop = statistics.mean(candidate[budget]["coverage"]) - statistics.mean(
            current[budget]["coverage"]
        )
        checks.append((f"coverage at {budget}: no drop over 0.5 pt", drop >= -0.005,
                       f"{drop:+.4f}"))  # fmt: skip
    return checks


def confirm_dedup(
    candidate: Mapping[int, Mapping[str, Sequence[Any]]],
    current: Mapping[int, Mapping[str, Sequence[Any]]],
    strong_per_1k: Mapping[int, float],
    tokens: Mapping[int, Sequence[float]],
) -> list[tuple[str, bool, str]]:
    """DECISIONS 2026-10-28, item 5: less repetition, nothing lost."""
    checks = []
    for budget in candidate:
        cov = statistics.mean(candidate[budget]["coverage"])
        cov_drop = cov - statistics.mean(current[budget]["coverage"])
        hit_drop = statistics.mean(candidate[budget]["hit"]) - statistics.mean(
            current[budget]["hit"]
        )
        checks.append((f"coverage at {budget}: no drop over 0.5 pt",
                       cov_drop >= -0.005, f"{cov_drop:+.4f}"))  # fmt: skip
        checks.append((f"hit at {budget}: no drop over 1 pt", hit_drop >= -0.01,
                       f"{hit_drop:+.4f}"))  # fmt: skip
        ours = 1000 * cov / statistics.mean(tokens[budget])
        checks.append((f"coverage per 1k tokens at {budget} > strong_rag",
                       ours > strong_per_1k[budget],
                       f"{ours:.3f} vs {strong_per_1k[budget]:.3f}"))  # fmt: skip
        if budget >= 4_000:
            gain = statistics.mean(candidate[budget]["precision"]) - statistics.mean(
                current[budget]["precision"]
            )
            checks.append((f"token precision at {budget} rises", gain > 0,
                           f"{gain:+.4f}"))  # fmt: skip
    return checks


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    import csv

    from app.config import get_settings
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import BUDGETS, bench_task
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider
    from bench.runners.ours import OursRunner
    from bench.section_recall import units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", choices=["order", "edges", "dedup"])
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
    prepared = [
        runner.prepare_candidates(bench_task(tasks[k["task_id"]]), store=store)
        for k in keys
    ]
    sections = [k["section"] for k in keys]
    section_units = [units(s) for s in sections]

    original = ours_module.select
    measured: dict[str, dict[int, dict[str, list[Any]]]] = {}
    token_counts: dict[str, dict[int, list[float]]] = {}
    dup_share: dict[int, float] = {}
    dup_tokens: dict[int, float] = {}
    try:
        for variant in VARIANTS:
            for budget in BUDGETS:
                contexts, used = [], []
                for prep in prepared:

                    def text_of(c: Candidate, prep: Any = prep) -> str:
                        return str(prep.md_by_key[(c.document_id, c.ord)])

                    chosen: list[list[Candidate]] = []
                    patched = variant_select(variant, text_of, chosen)
                    ours_module.select = patched  # type: ignore[assignment]
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    contexts.append(result.text)
                    used.append(float(result.context_tokens))
                    if variant == "current":
                        picked = chosen[0] if chosen else []
                        wasted = near_dup_tokens(picked, text_of)
                        dup_share[budget] = dup_share.get(budget, 0.0) + (wasted > 0)
                        dup_tokens[budget] = dup_tokens.get(budget, 0.0) + wasted
                cell = measure_cell(contexts, sections, section_units)
                measured.setdefault(variant, {})[budget] = cell
                token_counts.setdefault(variant, {})[budget] = used
    finally:
        ours_module.select = original

    n = len(keys)
    out = [f"# M10a order and near duplicates, half {args.half} ({n} tasks)", ""]
    out += ["## Near duplicates in the current selection", "",
            "| budget | selections with a pair | mean tokens wasted |",
            "|---|---|---|"]  # fmt: skip
    out += [
        f"| {b} | {dup_share[b] / n:.3f} | {dup_tokens[b] / n:,.0f} |" for b in BUDGETS
    ]
    out += ["", "## Variants", "",
            "| variant | budget | hit | coverage | tokens | answer first "
            "| answer at an end | median first_hit | token precision |",
            "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for variant in VARIANTS:
        for b in BUDGETS:
            c = measured[variant][b]
            hits = [f for f in c["first_hit"] if f is not None]
            first = sum(f == 1 for f in hits) / len(hits) if hits else 0.0
            med = statistics.median(hits) if hits else 0
            hit_edges = [e for e, h in zip(c["edge"], c["hit"], strict=True) if h]
            edge = sum(hit_edges) / len(hit_edges) if hit_edges else 0.0
            out.append(
                f"| {variant} | {b} | {statistics.mean(c['hit']):.3f} "
                f"| {statistics.mean(c['coverage']):.3f} "
                f"| {statistics.mean(token_counts[variant][b]):,.0f} | {first:.3f} "
                f"| {edge:.3f} | {med} | {statistics.mean(c['precision']):.3f} |"
            )
    worst = max(dup_share[b] / n for b in BUDGETS)
    if worst < MIN_DUP_SHARE:
        out += ["", f"Item 5: pairs in at most {worst:.3f} of selections "
                f"(< {MIN_DUP_SHARE}); dedup is rejected without half B."]  # fmt: skip
    name = f"order_dedup_{args.half}"
    if args.confirm:
        name = f"confirm_{args.confirm}_{args.half}"
        if args.confirm in ("order", "edges"):
            checks = confirm_order(measured[args.confirm], measured["current"])
        else:
            base = {
                (r["task_id"], int(r["budget"])): r
                for r in csv.DictReader(
                    (_OUT / "baseline_real.csv").open(encoding="utf-8")
                )
                if r["system"] == "strong_rag"
            }
            ids = [k["task_id"] for k in keys]
            strong = {
                b: 1000
                * statistics.mean(float(base[(t, b)]["coverage"]) for t in ids)
                / statistics.mean(float(base[(t, b)]["tokens"]) for t in ids)
                for b in BUDGETS
            }
            checks = confirm_dedup(measured["dedup"], measured["current"], strong,
                                   token_counts["dedup"])  # fmt: skip
        out += ["", f"## Acceptance: {args.confirm} vs current", ""]
        out += [f"- {'PASS' if ok else 'FAIL'} {w}: {d}" for w, ok, d in checks]
        verdict = "ACCEPTED" if all(ok for _, ok, _ in checks) else "REJECTED"
        out += ["", f"**{verdict}**"]
    report = "\n".join(out) + "\n"
    (_OUT / f"{name}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
