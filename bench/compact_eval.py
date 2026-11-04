"""M10b L7: does ``compact`` keep the answer in fewer tokens? (dev slice only)

The low-budget mode (``app/retrieve/compact.py``) selects chunks with a pool of
4 × B tokens, then keeps only the query-relevant sentences within B. Section
coverage (``bench.section_recall.coverage``, the M10a hit rule) counts the
share of the answer-key section's units present, so pruning irrelevant
sentences INSIDE the key section lowers it by construction even when the
answer survives (docs/pesquisa/rodada4/K_eficiencia.md, L7). This script adds
the complementary metrics, fixed here before any measurement:

* ``answer_hit``: at least one key unit with at least :data:`ANSWER_MIN_WORDS`
  words is present in the context (a section without such a unit falls back
  to its units of ``MIN_WORDS``+ words). It asks "did an answer-bearing
  sentence arrive?", which pruning cannot game by dropping filler.
* ``recall_per_1k``: answer-bearing sentence recall per token, i.e. mean
  section coverage over mean context tokens, × 1000.
* ``token_precision``: the existing share of context tokens that belong to the
  section (``bench.section_recall.token_precision``).
* Section coverage hit (``>= HIT``) is reported but not gated.

Both arms see the same cached candidates (selection only, like
``bench.m10a_selection``) and the same ``[URL]`` headers and
``truncate_to_budget``: ``current`` is NINA at budget B; ``compact`` selects at
:func:`app.retrieve.compact.pool_budget` (B) and compacts to B. Budgets:
:data:`BUDGETS`. Zero cost: providers come from the disk cache and a miss stops
the run with ``PaidCallRefused`` instead of calling a paid API (DECISIONS.md
2026-11-04: it used to call them, and half A paid for its misses).

**Acceptance rule (DECISIONS.md 2026-10-29, written before measuring).** The
constants of ``app/retrieve/compact.py`` are frozen; there is no tuning.

1. Half A (gate): if ``answer_hit`` at 1000 does not rise over ``current``
   (point estimate <= 0), ``compact`` stays an opt-in mode and is not proposed
   for any default; half B is not opened.
2. Half B (confirm): ``compact`` PASSES only if all of these hold:
   a. ``answer_hit`` at 1000: paired difference compact − current with the 95%
      bootstrap CI lower bound > 0 (``bench.m10a_diagnose.paired``);
   b. ``answer_hit`` at 500: point estimate > 0;
   c. ``recall_per_1k`` at 500 and at 1000 above ``current``, and at 1000
      above ``strong_rag`` (Phase 1 baseline, ``baseline_real.csv``);
   d. mean ``token_precision`` at 1000 not below ``current``;
   e. ``answer_hit`` at 2000 does not drop more than 1 point.
3. A PASS only makes ``compact`` eligible to be recommended (skill, or a
   default for ``max_tokens <= 1500``); changing a default is a separate
   contract decision for the human (CLAUDE.md rule 6).

Usage:
    uv run python -m bench.compact_eval --half A
    uv run python -m bench.compact_eval --half B
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.retrieve.compact import compact_pieces, pool_budget
from app.retrieve.select import Candidate
from bench.section_recall import MIN_WORDS, normalize

#: Budgets measured (L7 targets 500 and 1000; 2000 guards against harm).
BUDGETS = (500, 1_000, 2_000)
#: Shortest key unit that counts as answer-bearing for ``answer_hit``.
ANSWER_MIN_WORDS = 8
_OUT = Path("bench/results/m10a")


def answer_units(section_units: Sequence[str]) -> list[str]:
    """The key units long enough to carry an answer (fallback: all units)."""
    long = [u for u in section_units if len(u.split()) >= ANSWER_MIN_WORDS]
    return long or [u for u in section_units if len(u.split()) >= MIN_WORDS]


def answer_hit(section_units: Sequence[str], context: str) -> bool:
    """At least one answer-bearing key unit is in ``context``."""
    haystack = normalize(context)
    return any(unit in haystack for unit in answer_units(section_units))


def compact_context(
    question: str,
    selected: Sequence[Candidate],
    md_by_key: Mapping[tuple[str, int], str],
    url_by_document: Mapping[str, str],
    budget: int,
    *,
    unit_scorer: Callable[[Sequence[str]], Sequence[float]] | None = None,
) -> str:
    """The ``compact`` arm's context, emitted like ``OursRunner`` does."""
    keys = [(c.document_id, c.ord) for c in selected]
    outcome = compact_pieces(
        question,
        [md_by_key[k] for k in keys],
        [c.value for c in selected],
        budget,
        unit_scorer=unit_scorer,
    )
    parts = []
    for candidate, text in zip(selected, outcome.texts, strict=True):
        if text is None:
            continue
        url = url_by_document.get(candidate.document_id)
        parts.append(f"[URL] {url}\n{text}" if url is not None else text)
    return "\n\n".join(parts)


def per_1k(cell: Mapping[str, Sequence[float]]) -> float:
    """Mean coverage per thousand context tokens."""
    tokens = statistics.mean(cell["tokens"])
    return 1000 * statistics.mean(cell["coverage"]) / tokens if tokens else 0.0


def gate_half_a(cells: Mapping[str, Mapping[int, Mapping[str, Sequence[Any]]]]) -> bool:
    """Rule 1: does ``answer_hit`` at 1000 rise on half A?"""
    new = statistics.mean(cells["compact"][1_000]["answer_hit"])
    old = statistics.mean(cells["current"][1_000]["answer_hit"])
    return new - old > 0


def confirm(
    cells: Mapping[str, Mapping[int, Mapping[str, Sequence[Any]]]],
    strong_per_1k_at_1000: float,
) -> list[tuple[str, bool, str]]:
    """Rule 2 on half B, one ``(check, passed, detail)`` row per item."""
    from bench.m10a_diagnose import paired

    new, old = cells["compact"], cells["current"]
    p = paired(new[1_000]["answer_hit"], old[1_000]["answer_hit"])
    rows = [
        (
            "a. answer_hit at 1000: CI lower bound > 0",
            p["lo"] > 0,
            f"{p['diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}], p={p['p']:.4f}",
        )
    ]
    diff500 = statistics.mean(new[500]["answer_hit"]) - statistics.mean(
        old[500]["answer_hit"]
    )
    rows.append(("b. answer_hit at 500 rises", diff500 > 0, f"{diff500:+.3f}"))
    for budget in (500, 1_000):
        ours, theirs = per_1k(new[budget]), per_1k(old[budget])
        rows.append((f"c. recall per 1k at {budget} > current", ours > theirs,
                     f"{ours:.3f} vs {theirs:.3f}"))  # fmt: skip
    ours = per_1k(new[1_000])
    rows.append(("c. recall per 1k at 1000 > strong_rag",
                 ours > strong_per_1k_at_1000,
                 f"{ours:.3f} vs {strong_per_1k_at_1000:.3f}"))  # fmt: skip
    prec_new = statistics.mean(new[1_000]["precision"])
    prec_old = statistics.mean(old[1_000]["precision"])
    precision = f"{prec_new:.3f} vs {prec_old:.3f}"
    rows.append(("d. token precision at 1000 not below current",
                 prec_new >= prec_old, precision))  # fmt: skip
    drop = statistics.mean(new[2_000]["answer_hit"]) - statistics.mean(
        old[2_000]["answer_hit"]
    )
    rows.append(("e. answer_hit at 2000: drop at most 1 pt", drop >= -0.01,
                 f"{drop:+.3f}"))  # fmt: skip
    return rows


def _strong_rag_per_1k(task_ids: Sequence[str]) -> float:  # pragma: no cover
    rows = {
        (r["task_id"], int(r["budget"])): r
        for r in csv.DictReader((_OUT / "baseline_real.csv").open(encoding="utf-8"))
        if r["system"] == "strong_rag"
    }
    cell = {
        "coverage": [float(rows[(t, 1_000)]["coverage"]) for t in task_ids],
        "tokens": [float(rows[(t, 1_000)]["tokens"]) for t in task_ids],
    }
    return per_1k(cell)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.config import get_settings
    from app.index.tokens import count_tokens
    from app.retrieve.select import select
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import bench_task
    from bench.m10b_retrieval import cached_jina, cached_openai
    from bench.runners.base import truncate_to_budget
    from bench.runners.ours import OursRunner
    from bench.section_recall import HIT, coverage, token_precision, units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    args = parser.parse_args(argv)

    embeddings = cached_openai()
    reranker = cached_jina()
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
    cells: dict[str, dict[int, dict[str, list[Any]]]] = {}

    def record(arm: str, budget: int, ctx: str, section: str, su: list[str]) -> None:
        cell = cells.setdefault(arm, {}).setdefault(
            budget,
            {
                "answer_hit": [],
                "hit": [],
                "coverage": [],
                "tokens": [],
                "precision": [],
            },
        )
        cov = coverage(su, ctx)
        cell["answer_hit"].append(answer_hit(su, ctx))
        cell["hit"].append(cov >= HIT)
        cell["coverage"].append(cov)
        cell["tokens"].append(count_tokens(ctx))
        cell["precision"].append(token_precision(section, ctx).precision)

    for key in keys:
        task = bench_task(tasks[key["task_id"]])  # the question NINA is given
        prep = runner.prepare_candidates(task, store=store)
        su = units(key["section"])
        for budget in BUDGETS:
            current = runner.assemble_from_candidates(prep, budget_tokens=budget)
            record("current", budget, current.text, key["section"], su)
            pool = select(
                prep.candidates,
                budget_tokens=pool_budget(budget),
                lambda_=settings.selection_lambda,
                min_value=settings.min_value,
            )
            ctx = compact_context(
                task.question, pool, prep.md_by_key, prep.url_by_document, budget
            )
            ctx, _ = truncate_to_budget(ctx, budget)
            record("compact", budget, ctx, key["section"], su)

    out = [f"# M10b L7: compact vs current, half {args.half} ({len(keys)} tasks)", ""]
    out += ["| arm | budget | answer_hit | hit | recall/1k | precision | tokens |",
            "|---|---|---|---|---|---|---|"]  # fmt: skip
    for arm, by_budget in cells.items():
        for budget, cell in by_budget.items():
            out.append(
                f"| {arm} | {budget} | {statistics.mean(cell['answer_hit']):.3f} "
                f"| {statistics.mean(cell['hit']):.3f} | {per_1k(cell):.3f} "
                f"| {statistics.mean(cell['precision']):.3f} "
                f"| {statistics.mean(cell['tokens']):,.0f} |"
            )
    if args.half == "A":
        verdict = "OPEN HALF B" if gate_half_a(cells) else "STOP (rule 1)"
        out += ["", f"**Gate: {verdict}**"]
    else:
        strong = _strong_rag_per_1k([k["task_id"] for k in keys])
        rows = confirm(cells, strong)
        out += ["", "## Acceptance (rule 2)", ""]
        out += [f"- {'PASS' if ok else 'FAIL'} {what}: {detail}"
                for what, ok, detail in rows]  # fmt: skip
        out += ["", f"**{'ACCEPTED' if all(ok for _, ok, _ in rows) else 'REJECTED'}**"]
    report = "\n".join(out) + "\n"
    (_OUT / f"compact_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
