"""M10a track A, A1: stop adding fragments when rerank relevance drops.

Exploration only: swaps the ``select`` the ours runner calls for a variant, so
no ``app/`` code changes until one is accepted (rule: DECISIONS.md 2026-10-17).
Candidates are prepared once per task through the disk-cached providers with
the extraction as it was before the ``<pre>`` line-break fix, so no chunk text
changes and no new spend is expected (the paid-call counts are printed).

Usage:
    uv run python -m bench.m10a_adaptive --half A
    uv run python -m bench.m10a_adaptive --half B --confirm abs_0.2
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import app.extract.html as html_module
import bench.runners.ours as ours_module
from app.config import Settings
from app.retrieve.select import Candidate, _adjusted_value, select
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_selection import _strong_rag, per_1k
from bench.runners.ours import OursRunner
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: name -> (kind, parameter); kind is "none", "abs", "rel" or "elbow".
VARIANTS: dict[str, tuple[str, float]] = {
    "current": ("none", 0.0),
    "abs_0.1": ("abs", 0.1),
    "abs_0.2": ("abs", 0.2),
    "abs_0.3": ("abs", 0.3),
    "rel_0.3": ("rel", 0.3),
    "rel_0.5": ("rel", 0.5),
    "elbow_0.5": ("elbow", 0.5),
}
_OUT = Path("bench/results/m10a")


def select_adaptive(
    candidates: Sequence[Candidate],
    *,
    budget_tokens: int,
    lambda_: float,
    min_value: float,
    kind: str,
    param: float,
) -> list[Candidate]:
    """The app selector after dropping low-relevance candidates.

    ``abs``: keep values >= ``param``; ``rel``: keep values >= ``param`` x the
    task's highest value; ``elbow``: stop once the next pick's value is below
    ``param`` x the previous pick's value.
    """
    pool = list(candidates)
    if kind == "abs":
        pool = [c for c in pool if c.value >= param]
    elif kind == "rel" and pool:
        top = max(c.value for c in pool)
        pool = [c for c in pool if c.value >= param * top]
    if kind != "elbow":
        return select(pool, budget_tokens=budget_tokens, lambda_=lambda_,
                      min_value=min_value)  # fmt: skip
    selected: list[Candidate] = []
    budget_left = budget_tokens
    while pool:
        fitting = [c for c in pool if 0 < c.tokens <= budget_left]
        if not fitting:
            break
        best = max(fitting, key=lambda c: _adjusted_value(c, selected, lambda_))
        if _adjusted_value(best, selected, lambda_) < min_value:
            break
        if selected and best.value < param * selected[-1].value:
            break
        selected.append(best)
        budget_left -= best.tokens
        pool.remove(best)
    selected.sort(key=lambda c: (c.document_id, c.ord))
    return selected


Cells = Mapping[int, Mapping[str, Sequence[Any]]]


def accept(
    candidate: Cells, current: Cells, strong: Cells, *, half_a: bool = False
) -> list[tuple[str, bool, str]]:
    """The A1 rule (DECISIONS 2026-10-17); on half A, criteria (1) and (3)."""
    checks = []
    for b in candidate:
        cov = statistics.mean(candidate[b]["coverage"]) - statistics.mean(
            current[b]["coverage"]
        )
        hit = statistics.mean(candidate[b]["hit"]) - statistics.mean(current[b]["hit"])
        checks.append((f"{b}: coverage drop <= 0.5 pt and hit drop <= 1 pt",
                       cov >= -0.005 and hit >= -0.01,
                       f"coverage {cov:+.4f}, hit {hit:+.3f}"))  # fmt: skip
    if not half_a:
        for b in (4000, 8000):
            ratio = statistics.mean(candidate[b]["tokens"]) / statistics.mean(
                current[b]["tokens"]
            )
            checks.append((f"{b}: tokens fall >= 5%", ratio <= 0.95,
                           f"{ratio - 1:+.1%}"))  # fmt: skip
    for b in candidate:
        ours, theirs = per_1k(candidate[b]), per_1k(strong[b])
        checks.append((f"{b}: coverage per 1k > strong_rag", ours > theirs,
                       f"{ours:.3f} vs {theirs:.3f}"))  # fmt: skip
    return checks


def _patched(kind: str, param: float) -> Callable[..., list[Candidate]]:
    def patched(candidates, *, budget_tokens, lambda_, min_value):  # type: ignore[no-untyped-def]
        return select_adaptive(candidates, budget_tokens=budget_tokens,
                               lambda_=lambda_, min_value=min_value,
                               kind=kind, param=param)  # fmt: skip

    return patched


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", help="variant to test against current")
    args = parser.parse_args(argv)
    names = ["current", args.confirm] if args.confirm else list(VARIANTS)

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
    new_raw_code = html_module._raw_code
    html_module._raw_code = lambda el: el.text_content() or ""  # pre-fix extraction
    try:
        prepared = [
            (key, runner.prepare_candidates(bench_task(tasks[key["task_id"]]),
                                            store=store))
            for key in keys
        ]  # fmt: skip
    finally:
        html_module._raw_code = new_raw_code

    measured: dict[str, dict[int, dict[str, list[Any]]]] = {}
    original = ours_module.select
    try:
        for name in names:
            ours_module.select = _patched(*VARIANTS[name])  # type: ignore[assignment]
            measured[name] = {}
            for budget in BUDGETS:
                cell: dict[str, list[Any]] = {"hit": [], "coverage": [], "tokens": []}
                for key, prep in prepared:
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    cov = coverage(units(key["section"]), result.text)
                    cell["hit"].append(cov >= HIT)
                    cell["coverage"].append(cov)
                    cell["tokens"].append(result.context_tokens)
                measured[name][budget] = cell
    finally:
        ours_module.select = original

    strong = _strong_rag([key["task_id"] for key, _ in prepared])
    lines = [
        f"# M10a A1 adaptive budget, half {args.half} ({len(keys)} tasks)",
        "",
        "| variant | "
        + " | ".join(f"coverage {b} | hit {b} | tokens {b}" for b in BUDGETS)
        + " | passes (1)+(3) |",
        "|---" * (2 + 3 * len(BUDGETS)) + "|",
    ]
    for name in names:
        cells = [
            f"{statistics.mean(measured[name][b]['coverage']):.3f} | "
            f"{statistics.mean(measured[name][b]['hit']):.3f} | "
            f"{statistics.mean(measured[name][b]['tokens']):,.0f}"
            for b in BUDGETS
        ]
        ok = all(o for _, o, _ in accept(measured[name], measured["current"], strong,
                                         half_a=True))  # fmt: skip
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {ok} |")
    out = f"adaptive_{args.half}"
    if args.confirm:
        out = f"confirm_{args.confirm}_{args.half}"
        checks = accept(measured[args.confirm], measured["current"], strong)
        lines += ["", f"## Acceptance: {args.confirm} vs current", ""]
        lines += [f"- {'PASS' if ok else 'FAIL'} {what}: {detail}"
                  for what, ok, detail in checks]  # fmt: skip
        verdict = "ACCEPTED" if all(ok for _, ok, _ in checks) else "REJECTED"
        lines += ["", f"**{verdict}**"]
    report = "\n".join(lines) + "\n"
    (_OUT / f"{out}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
