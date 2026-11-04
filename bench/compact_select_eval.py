"""Which way of choosing sentences finds more answers within the web scope's read?

Local models only: the running API builds the pool of whole chunks for the page that
holds the answer, the local reranker scores units (cached), and this module runs the
product's own compaction and render at :data:`BUDGET`. No paid call and no Context7
call. The question is the task's title, as in :mod:`bench.context7_compare`.

**Arms (fixed before measuring).** Research suggests scoring a sentence with its
context (EXIT, arXiv 2412.12559) and looking past fixed cuts (AdaComp, LooComp); a
real reply showed short lines picked for repeating a term.

* ``short5``: a text unit under 5 words scores 0 of its own;
* ``context``: the reranker reads the unit under its heading;
* ``heading``: a unit gains a quarter of its heading's score;
* ``paragraph``: half the score comes from the unit's whole line or paragraph;
* ``density_quarter`` / ``density_none``: fill by score / tokens^0.25 or by score alone,
  instead of score / sqrt(tokens), which favors short units;
* ``window``: neighbours on the same line gain 0.5 of a unit's score, not 0.25.

**Acceptance rule (written before measuring, DECISIONS.md 2026-11-04).**

1. Half A (138 tasks) chooses. An arm advances if its ``soft_hit`` is at least 3 points
   above ``base`` with mean tokens <= :data:`BUDGET`. The best one advances, unless the
   combination of the two best singles, measured once, is better.
2. Half B (the 110 tasks with Context7's stored replies, never used to choose a
   selection) decides. The arm is adopted if its ``soft_hit`` is at least 2 points above
   ``base`` and, where the two disagree, a blind reader says yes to the arm at least as
   often as to ``base``.
3. Nothing advancing or a failed half B leaves the product as it is. Fix loops stop
   after two rounds with no progress.

An adopted arm is then compared with ``c7_fetch`` on those tasks and reported, but those
replies were already seen, and new Context7 calls wait for its quota.

    uv run python -m bench.compact_select_eval --half A
    uv run python -m bench.compact_select_eval --half A --arms base,short5+context
    uv run python -m bench.compact_select_eval --half B --arms base,context
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag

from app.retrieve import compact as compact_module
from bench.context7_compare import soft_hit

BUDGET = 560
POOL = compact_module.POOL_FACTOR * BUDGET
MIN_GAIN_TUNE = 0.03
MIN_GAIN_CONFIRM = 0.02
_OUT = Path("bench/results/m10b/select")


@dataclass(frozen=True)
class Arm:
    """One way of scoring and filling; the defaults are the product's."""

    short_min: int = 0
    context: bool = False
    heading_weight: float = 0.0
    paragraph_weight: float = 0.0
    exponent: float = compact_module.DENSITY_EXPONENT
    window: float = compact_module.WINDOW_WEIGHT


ARMS = {
    "base": Arm(),
    "short5": Arm(short_min=5),
    "context": Arm(context=True),
    "heading": Arm(heading_weight=0.25),
    "paragraph": Arm(paragraph_weight=0.5),
    "density_quarter": Arm(exponent=0.25),
    "density_none": Arm(exponent=0.0),
    "window": Arm(window=0.5),
}


def combined(*names: str) -> Arm:
    """The arm that applies each named arm's change."""
    default = Arm()
    merged = default
    for name in names:
        changes = {
            f.name: getattr(ARMS[name], f.name)
            for f in fields(Arm)
            if getattr(ARMS[name], f.name) != getattr(default, f.name)
        }
        merged = replace(merged, **changes)
    return merged


def arm_of(spec: str) -> Arm:
    """``"a"`` or ``"a+b"``: one arm or a combination."""
    return combined(*spec.split("+"))


def advances(hit: float, base_hit: float, tokens: float) -> bool:
    """Rule 1 of the module docstring."""
    return round(hit - base_hit, 6) >= MIN_GAIN_TUNE and tokens <= BUDGET


def adopted(hit: float, base_hit: float, only_arm: int, only_base: int) -> bool:
    """Rule 2 of the module docstring."""
    return round(hit - base_hit, 6) >= MIN_GAIN_CONFIRM and only_arm >= only_base


def scored(
    arm: Arm,
    score: Callable[[Sequence[str]], Sequence[float]],
    texts: Sequence[str],
    units: Sequence[Any],
    titles: Sequence[str] = (),
) -> list[float]:
    """Each unit's own score under ``arm``; headings stay at 0, as in the product.

    ``titles`` is each piece's last heading: chunks hold it apart from their text.
    """

    def text(unit: Any) -> str:
        return str(texts[unit.piece][unit.start : unit.end])

    by_key = {(u.piece, u.index): u for u in units}

    def heading_of(unit: Any) -> str:
        if unit.heading is None:
            return titles[unit.piece] if titles else ""
        return text(by_key[(unit.piece, unit.heading)]).lstrip("# ").strip()

    body = [u for u in units if u.kind != "heading"]
    if not body:
        return [0.0 for _ in units]
    inputs = []
    for unit in body:
        title = heading_of(unit) if arm.context else ""
        inputs.append(f"{title}\n{text(unit)}" if title else text(unit))
    own = dict(zip(((u.piece, u.index) for u in body), score(inputs), strict=True))
    if arm.heading_weight:
        titles = list(dict.fromkeys(t for t in map(heading_of, body) if t))
        values = dict(zip(titles, score(titles), strict=True)) if titles else {}
        for unit in body:
            bonus = arm.heading_weight * values.get(heading_of(unit), 0.0)
            own[(unit.piece, unit.index)] += bonus
    if arm.paragraph_weight:
        groups: dict[tuple[int, int], list[str]] = {}
        for unit in body:
            groups.setdefault((unit.piece, unit.group), []).append(text(unit))
        joined = {key: " ".join(parts) for key, parts in groups.items()}
        whole = dict(zip(joined, score(list(joined.values())), strict=True))
        weight = arm.paragraph_weight
        for unit in body:
            key = (unit.piece, unit.index)
            own[key] = (1 - weight) * own[key] + weight * whole[
                (unit.piece, unit.group)
            ]
    if arm.short_min:
        for unit in body:
            if unit.kind == "text" and len(text(unit).split()) < arm.short_min:
                own[(unit.piece, unit.index)] = 0.0
    return [own.get((u.piece, u.index), 0.0) for u in units]


def _reply(  # pragma: no cover - runs the product's pipeline on stored pools
    arm: Arm, query: str, pool: Mapping[str, Any], score: Any
) -> str:
    from app.api.assemble import _fit_rendered
    from app.index.tokens import count_tokens
    from app.retrieve.assemble import (
        AssembleResult,
        Fragment,
        FragmentScores,
        SourceStatus,
    )
    from app.retrieve.compact import compact_result, pool_budget
    from app.retrieve.render import render_concise

    fragments = [
        Fragment(
            chunk_id=f["chunk_id"],
            document_id=f["document_id"],
            version_id=f["version_id"],
            source_url=f["source_url"],
            heading_path=f["heading_path"],
            md=f["md"],
            tokens=count_tokens(f["md"]),
            scores=FragmentScores(**f["scores"]),
            evidence=[],
        )
        for f in pool["fragments"]
    ]
    result = AssembleResult(
        query=query,
        fragments=fragments,
        sources=[
            SourceStatus(
                s["url"],
                s.get("document_id"),
                s.get("version_id"),
                s.get("fetched_at"),
                s["status"],
                page_tokens=s.get("page_tokens"),
                date=s.get("date"),
            )
            for s in pool["sources"]
        ],
        budget_tokens=pool_budget(BUDGET),
        coverage="unknown",
        warnings=list(pool.get("warnings", [])),
    )
    saved = (
        compact_module._scored_by,
        compact_module.WINDOW_WEIGHT,
        compact_module.DENSITY_EXPONENT,
    )
    title_of = {f.md: f.heading_path[-1] if f.heading_path else "" for f in fragments}
    compact_module._scored_by = lambda scorer, texts, units: scored(
        arm, scorer, texts, units, [title_of.get(t, "") for t in texts]
    )
    compact_module.WINDOW_WEIGHT = arm.window
    compact_module.DENSITY_EXPONENT = arm.exponent
    try:
        pooled = (result.fragments, result.budget_limited)

        def compact(budget: int) -> None:
            compact_result(result, query=query, budget=budget, unit_scorer=score)

        compact(BUDGET)
        _fit_rendered(result, pooled, compact=compact, max_tokens=BUDGET)
        return render_concise(result)
    finally:
        (
            compact_module._scored_by,
            compact_module.WINDOW_WEIGHT,
            compact_module.DENSITY_EXPONENT,
        ) = saved


def _keys(half: str) -> list[dict[str, Any]]:  # pragma: no cover - local data
    from bench.anchor_qrels import in_tune_half
    from bench.context7_compare import _keys as seen
    from bench.context7_compare import _new_keys as new

    if half == "B":
        return seen() + new()
    lines = Path("bench/results/m10a/qrels.jsonl").read_text("utf-8").splitlines()
    return [k for k in map(json.loads, lines) if in_tune_half(k["task_id"])]


def main() -> None:  # pragma: no cover - local API and models
    from app.api.assemble import count_tokens
    from app.mcp_server.client import NinaApiClient
    from app.retrieve.compact import cached_scorer
    from bench.m10b_retrieval import local_reranker
    from bench.section_recall import units
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--reranker-url", default="http://127.0.0.1:8081")
    parser.add_argument(
        "--reranker-model", default="Qwen3-Reranker-0.6B-Q8_0@ggml-org-GGUF"
    )
    parser.add_argument("--score-transform", default="none")
    args = parser.parse_args()
    names = args.arms.split(",")
    reranker = local_reranker(args)
    api = NinaApiClient(base_url="http://127.0.0.1:8000", api_key="nina_local_dev")
    api.set_caller("teste-selecao")
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    folder = _OUT / args.half
    (folder / "pools").mkdir(parents=True, exist_ok=True)
    tally: dict[str, dict[str, list[float]]] = {
        n: {"hit": [], "tokens": []} for n in names
    }
    failed = 0
    keys = _keys(args.half)
    for done, key in enumerate(keys):
        if done % 10 == 0:
            print(f"{done}/{len(keys)}", flush=True)
        task_id = key["task_id"]
        query = tasks[task_id].question_title
        path = folder / "pools" / f"{task_id}.json"
        pool: dict[str, Any] | None = None
        if path.exists():
            pool = json.loads(path.read_text("utf-8"))
        else:
            try:
                pool = api.assemble(
                    query=query,
                    urls=[urldefrag(key["url"])[0]],
                    max_tokens=POOL,
                    response_format="detailed",
                )
                path.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:  # a page that fails is a measured miss
                print(f"pool failed for {task_id}: {type(exc).__name__}", flush=True)
                failed += 1
        section = units(key["section"])
        score = cached_scorer(reranker.rerank, query)
        for name in names:
            text = "" if pool is None else _reply(arm_of(name), query, pool, score)
            arm_dir = folder / name
            arm_dir.mkdir(exist_ok=True)
            (arm_dir / f"{task_id}.txt").write_text(text, encoding="utf-8")
            tally[name]["hit"].append(float(soft_hit(section, text)))
            tally[name]["tokens"].append(float(count_tokens(text)))
    base_hit = statistics.mean(tally["base"]["hit"]) if "base" in tally else 0.0
    lines = [
        f"# Sentence choice, half {args.half}, {len(keys)} tasks, {failed} failed",
        "",
        "| arm | soft hit | gain | mean tokens | advances |",
        "|---|---|---|---|---|",
    ]
    for name in names:
        hit = statistics.mean(tally[name]["hit"])
        mean_tokens = statistics.mean(tally[name]["tokens"])
        ok = name != "base" and advances(hit, base_hit, mean_tokens)
        lines.append(
            f"| {name} | {hit:.3f} | {hit - base_hit:+.3f} | {mean_tokens:,.0f} "
            f"| {'yes' if ok else ''} |"
        )
    report = "\n".join(lines) + "\n"
    with (_OUT / f"report_{args.half}.md").open("a", encoding="utf-8") as out:
        out.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
