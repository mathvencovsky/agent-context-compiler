"""Phase 2: can a score floor stop ``compact`` from filling the budget with noise?

Local models only, as the product runs them (Qwen3 embeddings and reranker on
loopback, cached in ``bench/.cache/local``): no paid call, every task measured.
Budget 1000, the web scope's fixed read. The base arm is the reranker without a
brake. A brake arm turns the units below its cutoff into 0, but always keeps the
best unit, so a page never falls back to whole chunks because nothing passed.

**Arms (fixed before measuring).** This server's scores run from about 0.9 (an
answer) to about 0.0001 (noise), and the floors already rejected (0.1, 0.5) sit
far above the noise. So: absolute floors in :data:`ABSOLUTE`, and floors relative
to the page's best unit in :data:`RELATIVE`.

**Acceptance rule (written before measuring, DECISIONS.md 2026-11-04).** On half A,
an arm passes if, against the base arm:

1. ``answer_hit`` falls by at most :data:`MAX_HIT_FALL` (2 points: the reranker
   gained 6 over BM25, and most of that must stay);
2. mean tokens are at most :data:`MAX_TOKEN_SHARE` of the base;
3. on mismatched questions (another task's question over this task's pages, where
   the honest answer is "nothing here"), mean tokens are at most
   :data:`MAX_MISMATCH_SHARE` of the base.

The passing arm with the fewest tokens goes to half B, opened once, with the same
three checks and no choice left. No arm passing means no brake: the product stays
as it is. A pass is evidence on documentation only and says nothing about cost.

Usage:
    uv run python -m bench.compact_stop_eval --half A
    uv run python -m bench.compact_stop_eval --half B --confirm abs_0.001
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from bench.compact_eval import answer_hit, compact_context

ABSOLUTE = (0.0001, 0.001, 0.01, 0.03)
RELATIVE = (0.001, 0.01, 0.05)
MAX_HIT_FALL = 0.02
MAX_TOKEN_SHARE = 0.80
MAX_MISMATCH_SHARE = 0.50
MISMATCH_EVERY = 3
BUDGET = 1_000
BASE = "base"
_OUT = Path("bench/results/m10b")
_QUERY_PREFIX = (
    "Instruct: Given a technical question, retrieve relevant code snippets or "
    "technical documentation that best answer the question\nQuery:"
)


def braked(
    score: Callable[[Sequence[str]], Sequence[float]],
    absolute: float = 0.0,
    relative: float = 0.0,
) -> Callable[[Sequence[str]], list[float]]:
    """``score`` with units below the cutoff at 0; the best unit always stays."""

    def scorer(texts: Sequence[str]) -> list[float]:
        values = list(score(texts))
        if not values:
            return []
        top = max(values)
        cutoff = max(absolute, relative * top)
        out = [s if s >= cutoff else 0.0 for s in values]
        out[values.index(top)] = max(top, 1e-9)
        return out

    return scorer


def arms() -> dict[str, Mapping[str, float]]:
    """Every arm's brake settings, the base arm first."""
    out: dict[str, Mapping[str, float]] = {BASE: {}}
    out.update({f"abs_{f}": {"absolute": f} for f in ABSOLUTE})
    out.update({f"rel_{f}": {"relative": f} for f in RELATIVE})
    return out


def mismatched(urls_by_task: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """``task -> other task``: the next task (sorted) on none of the same hosts."""
    ids = sorted(urls_by_task)
    hosts = {t: {urlsplit(u).netloc for u in urls_by_task[t]} for t in ids}
    out = {}
    for i, task in enumerate(ids):
        for other in ids[i + 1 :] + ids[:i]:
            if not hosts[task] & hosts[other]:
                out[task] = other
                break
    return out


def checks(
    base: Mapping[str, float], arm: Mapping[str, float]
) -> list[tuple[str, bool, str]]:
    """The acceptance rule, one ``(check, passed, detail)`` row per item."""
    fall = round(base["answer_hit"] - arm["answer_hit"], 6)
    share = arm["tokens"] / base["tokens"]
    wrong = arm["mismatch_tokens"] / base["mismatch_tokens"]
    return [
        ("1. answer_hit falls <= 2 pt", fall <= MAX_HIT_FALL, f"{-fall:+.3f}"),
        ("2. tokens <= 80% of base", share <= MAX_TOKEN_SHARE, f"{share:.0%}"),
        ("3. mismatched <= 50% of base", wrong <= MAX_MISMATCH_SHARE, f"{wrong:.0%}"),
    ]


def main() -> None:  # pragma: no cover - real corpus and local models
    from app.config import get_settings
    from app.index.tokens import count_tokens
    from app.retrieve.compact import cached_scorer, pool_budget
    from app.retrieve.select import select
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import bench_task
    from bench.m10b_retrieval import local_embeddings, local_reranker
    from bench.runners.base import truncate_to_budget
    from bench.runners.ours import OursRunner
    from bench.section_recall import units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    parser.add_argument("--confirm", help="the one arm half B judges")
    parser.add_argument("--embeddings-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--embeddings-model", default="Qwen3-Embedding-0.6B-Q8_0@Qwen-GGUF"
    )
    parser.add_argument("--embeddings-dim", type=int, default=1024)
    parser.add_argument("--query-prefix", default=_QUERY_PREFIX)
    parser.add_argument("--reranker-url", default="http://127.0.0.1:8081")
    parser.add_argument(
        "--reranker-model", default="Qwen3-Reranker-0.6B-Q8_0@ggml-org-GGUF"
    )
    parser.add_argument("--score-transform", default="none")
    args = parser.parse_args()
    if args.half == "B" and args.confirm not in arms():
        raise SystemExit("half B judges one arm: --confirm <arm from half A>")

    reranker = local_reranker(args)
    settings = get_settings()
    runner = OursRunner(
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
        lexical_weight=settings.lexical_weight,
        embeddings=local_embeddings(args),
        reranker=reranker,
    )
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    lines = Path("bench/results/m10a/qrels.jsonl").read_text("utf-8").splitlines()
    keys = [
        k
        for k in map(json.loads, lines)
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    others = mismatched({k["task_id"]: tasks[k["task_id"]].urls for k in keys})
    tally: dict[str, dict[str, list[float]]] = {
        arm: {"answer_hit": [], "tokens": [], "mismatch_tokens": []} for arm in arms()
    }

    def contexts(task_question: str, prep: object) -> dict[str, str]:
        score = cached_scorer(reranker.rerank, task_question)
        pool = select(
            prep.candidates,  # type: ignore[attr-defined]
            budget_tokens=pool_budget(BUDGET),
            lambda_=settings.selection_lambda,
            min_value=settings.min_value,
        )
        return {
            arm: truncate_to_budget(
                compact_context(
                    task_question,
                    pool,
                    prep.md_by_key,  # type: ignore[attr-defined]
                    prep.url_by_document,  # type: ignore[attr-defined]
                    BUDGET,
                    unit_scorer=braked(score, **brake),
                ),
                BUDGET,
            )[0]
            for arm, brake in arms().items()
        }

    for done, key in enumerate(keys):
        if done % 10 == 0:
            print(f"{done}/{len(keys)}", flush=True)
        task = bench_task(tasks[key["task_id"]])
        section = units(key["section"])
        for arm, ctx in contexts(
            task.question, runner.prepare_candidates(task, store=store)
        ).items():
            tally[arm]["answer_hit"].append(float(answer_hit(section, ctx)))
            tally[arm]["tokens"].append(float(count_tokens(ctx)))
        if done % MISMATCH_EVERY == 0 and key["task_id"] in others:
            question = bench_task(tasks[others[key["task_id"]]]).question
            wrong = task.model_copy(update={"question": question})
            for arm, ctx in contexts(
                question, runner.prepare_candidates(wrong, store=store)
            ).items():
                tally[arm]["mismatch_tokens"].append(float(count_tokens(ctx)))

    means = {
        arm: {name: statistics.mean(cell) for name, cell in row.items()}
        for arm, row in tally.items()
    }
    out = [
        f"# Relevance brake, half {args.half}, budget {BUDGET} ({len(keys)} tasks, "
        f"{len(tally[BASE]['mismatch_tokens'])} mismatched)",
        "",
        "| arm | answer_hit | tokens | mismatched tokens | passes |",
        "|---|---|---|---|---|",
    ]
    judged = [args.confirm] if args.half == "B" else [a for a in arms() if a != BASE]
    passing = []
    verdicts: list[str] = []
    for arm, row in means.items():
        rows = checks(means[BASE], row) if arm in judged else []
        ok = bool(rows) and all(passed for _, passed, _ in rows)
        if ok:
            passing.append(arm)
        out.append(
            f"| {arm} | {row['answer_hit']:.3f} | {row['tokens']:,.0f} "
            f"| {row['mismatch_tokens']:,.0f} | {'yes' if ok else ''} |"
        )
        if args.half == "B" and rows:
            verdicts += [f"{'PASS' if p else 'FAIL'} {c}: {d}" for c, p, d in rows]
    winner = min(passing, key=lambda a: means[a]["tokens"], default=None)
    out += ["", *verdicts, f"**Winner: {winner}**" if winner else "**REJECTED**"]
    report = "\n".join(out) + "\n"
    (_OUT / f"compact_stop_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
