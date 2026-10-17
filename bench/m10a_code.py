"""M10a track A, A2: favor chunks with a whole code block for code questions.

Exploration only (rule: DECISIONS.md 2026-10-17): the selector the ours runner
calls is swapped for one that multiplies a candidate's value by (1 + beta) when
the chunk holds a whole fenced code block and the query looks like a code
question. Candidates come from the disk-cached providers with the extraction as
it was before the ``<pre>`` fix, so no chunk text changes (paid-call counts are
printed).

Usage:
    uv run python -m bench.m10a_code --half A
    uv run python -m bench.m10a_code --half B --confirm code_0.2
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import app.extract.html as html_module
import bench.runners.ours as ours_module
from app.config import Settings
from app.retrieve.select import Candidate, select
from bench.anchor_qrels import in_tune_half
from bench.corpus_store import CorpusStore
from bench.m10a_baseline import BUDGETS, bench_task
from bench.m10a_selection import _strong_rag, per_1k
from bench.runners.ours import OursRunner, _IndexedChunk, _Key
from bench.section_recall import HIT, coverage, units
from bench.snapshot import DEFAULT_CORPUS_DIR
from bench.tasks_schema import load_external_tasks

#: name -> (beta, use the code-question detector)
VARIANTS: dict[str, tuple[float, bool]] = {
    "current": (0.0, True),
    "code_0.1": (0.1, True),
    "code_0.2": (0.2, True),
    "code_0.3": (0.3, True),
    "always_0.1": (0.1, False),
}
_OUT = Path("bench/results/m10a")

_CODE_QUERY = re.compile(
    r"\w\(|\(\)|\w\.\w+\(|\b\w+_\w+\b|`|(?-i:\b[a-z]+[A-Z]\w*\b)|=>|::|\{|\}|"
    r"\b(?:error|exception|traceback|typeerror|valueerror|syntaxerror|"
    r"function|method|class|import|return|async|await|regex|query|sql|"
    r"code|snippet|compile|syntax|argument|parameter)\b",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^```", re.MULTILINE)


def is_code_query(query: str) -> bool:
    """A query that names code: calls, identifiers, operators or code words."""
    return len(_CODE_QUERY.findall(query)) >= 2


def whole_code_keys(indexed: Sequence[_IndexedChunk]) -> set[_Key]:
    """Chunks with a complete fenced block that the chunker did not split."""
    owners = Counter(
        (item.document_id, b) for item in indexed for b in set(item.chunk.block_ids)
    )
    return {
        item.key
        for item in indexed
        if len(_FENCE.findall(item.chunk.md)) >= 2
        and all(owners[(item.document_id, b)] == 1 for b in item.chunk.block_ids)
    }


def boosted(
    candidates: Sequence[Candidate], keys: set[_Key], beta: float
) -> list[Candidate]:
    """``candidates`` with value x (1 + beta) for the chunks in ``keys``."""
    return [
        dataclasses.replace(c, value=c.value * (1 + beta))
        if (c.document_id, c.ord) in keys
        else c
        for c in candidates
    ]


Cells = Mapping[int, Mapping[str, Sequence[Any]]]


def _mean(cell: Mapping[str, Sequence[Any]], field: str, mask: Sequence[bool]) -> float:
    return statistics.mean(v for v, m in zip(cell[field], mask, strict=True) if m)


def accept(
    cand: Cells,
    cur: Cells,
    strong: Cells,
    code_mask: Sequence[bool],
    *,
    half_a: bool = False,
) -> list[tuple[str, bool, str]]:
    """The A2 rule (DECISIONS 2026-10-17); on half A, only criterion (2)."""
    every = [True] * len(code_mask)
    checks = []
    if not half_a:
        for b in cand:
            gain = _mean(cand[b], "coverage", code_mask) - _mean(
                cur[b], "coverage", code_mask
            )
            ok = gain > 0 if b >= 4000 else gain >= -0.005
            checks.append((f"code tasks {b}: coverage {'up' if b >= 4000 else 'held'}",
                           ok, f"{gain:+.4f}"))  # fmt: skip
    for b in cand:
        cov = _mean(cand[b], "coverage", every) - _mean(cur[b], "coverage", every)
        hit = _mean(cand[b], "hit", every) - _mean(cur[b], "hit", every)
        checks.append((f"all tasks {b}: coverage drop <= 0.5 pt, hit drop <= 1 pt",
                       cov >= -0.005 and hit >= -0.01,
                       f"coverage {cov:+.4f}, hit {hit:+.3f}"))  # fmt: skip
    if not half_a:
        for b in cand:
            ours, theirs = per_1k(cand[b]), per_1k(strong[b])
            checks.append((f"{b}: coverage per 1k > strong_rag", ours > theirs,
                           f"{ours:.3f} vs {theirs:.3f}"))  # fmt: skip
    return checks


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
    prepared = []
    try:
        for key in keys:
            task = bench_task(tasks[key["task_id"]])
            prep = runner.prepare_candidates(task, store=store)
            indexed, _ = runner._index_task(task, store)
            prepared.append((key, task.question, prep, whole_code_keys(indexed)))
    finally:
        html_module._raw_code = new_raw_code

    code_mask = [tasks[k["task_id"]].task_type == "code" for k, *_ in prepared]
    detected = [is_code_query(q) for _, q, _, _ in prepared]
    tp = sum(d and c for d, c in zip(detected, code_mask, strict=True))
    detector = (
        f"detector: flags {sum(detected)} of {len(detected)} queries; precision "
        f"{tp / max(sum(detected), 1):.2f}, recall {tp / max(sum(code_mask), 1):.2f} "
        "against task_type == code"
    )

    measured: dict[str, dict[int, dict[str, list[Any]]]] = {}
    original = ours_module.select
    try:
        for name in names:
            beta, use_detector = VARIANTS[name]
            measured[name] = {b: {"hit": [], "coverage": [], "tokens": []}
                              for b in BUDGETS}  # fmt: skip
            for (key, _query, prep, whole), flag in zip(
                prepared, detected, strict=True
            ):
                boost = beta if (flag or not use_detector) else 0.0

                def patched(candidates, *, budget_tokens, lambda_, min_value,  # type: ignore[no-untyped-def]
                            _w=whole, _b=boost) -> list[Candidate]:  # fmt: skip
                    return select(boosted(candidates, _w, _b),
                                  budget_tokens=budget_tokens, lambda_=lambda_,
                                  min_value=min_value)  # fmt: skip

                ours_module.select = patched  # type: ignore[assignment]
                for budget in BUDGETS:
                    result = runner.assemble_from_candidates(prep, budget_tokens=budget)
                    cov = coverage(units(key["section"]), result.text)
                    cell = measured[name][budget]
                    cell["hit"].append(cov >= HIT)
                    cell["coverage"].append(cov)
                    cell["tokens"].append(result.context_tokens)
    finally:
        ours_module.select = original

    strong = _strong_rag([k["task_id"] for k, *_ in prepared])
    every = [True] * len(code_mask)
    lines = [
        f"# M10a A2 code-aware selection, half {args.half} ({len(keys)} tasks, "
        f"{sum(code_mask)} code)",
        "",
        detector,
        "",
        "| variant | " + " | ".join(f"code cov {b} | all cov {b}" for b in BUDGETS)
        + " | passes (2) |",
        "|---" * (2 + 2 * len(BUDGETS)) + "|",
    ]  # fmt: skip
    for name in names:
        cells = [
            f"{_mean(measured[name][b], 'coverage', code_mask):.4f} | "
            f"{_mean(measured[name][b], 'coverage', every):.4f}"
            for b in BUDGETS
        ]
        ok = all(o for _, o, _ in accept(measured[name], measured["current"], strong,
                                         code_mask, half_a=True))  # fmt: skip
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {ok} |")
    out = f"code_{args.half}"
    if args.confirm:
        out = f"confirm_{args.confirm}_{args.half}"
        checks = accept(measured[args.confirm], measured["current"], strong, code_mask)
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
