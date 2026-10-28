"""M10a U2: prefer the documentation release the question names (dev only).

Retrieved text from the wrong release misleads models: they adopt incorrect
retrieved content over their own correct knowledge more than 60% of the time
(ClashEval, Stanford, NeurIPS 2024), and the best models get only about half of
version-dependent code right (GitChameleon 2.0, NeurIPS 2025). NINA's
``mixed_versions`` hint (P7) only warns. This variant goes one step further:
when the question names a release ("Python 3.11", "React 18", "PostgreSQL 16"),
candidates from a page of that release gain value and candidates from a page of
another numbered release lose it, before selection.

* A page's release is :func:`app.retrieve.hints.version_label` of its URL.
* ``exact``: the label equals the named release, or is a prefix of it at a dot
  boundary that names the same major release ("3" for "3.11" is not exact,
  it is neutral: one page covers many minors).
* ``other``: a numbered label that differs from the named release.
* Moving labels (``current``, ``stable``, ``latest``) and unlabeled pages stay
  neutral.

Value ×(1+β) for ``exact``, ×(1−β) for ``other``, β = :data:`BETA`, only when
the question names a release. Selection-only on cached candidates, like
``bench.m10a_selection``. Rule fixed before the first run (DECISIONS.md
2026-10-28): on half A, if fewer than :data:`MIN_TASKS` keyed tasks name a
release and have candidates from two or more releases, U2 stops for lack of
power. Otherwise it passes on half B if, on those tasks, mean coverage rises
(point estimate > 0) at 4k and 8k and, over all tasks, no budget loses more
than 0.5 pt of coverage.

Usage:
    uv run python -m bench.m10a_version --half A
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from app.retrieve.hints import version_label
from app.retrieve.select import Candidate

BETA = 0.2
MIN_TASKS = 20
_MOVING = {"current", "stable", "latest", "dev", "devel", "main", "master", "nightly"}
_NAMED = re.compile(
    r"\b(python|react|postgres(?:ql)?|kubernetes|k8s|flask|angular|docker|sqlite)"
    r"\s*v?(\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)


def named_release(question: str) -> str | None:
    """The first ``<product> <number>`` release the question names, if any."""
    m = _NAMED.search(question)
    return m.group(2) if m else None


def match(label: str | None, release: str) -> str:
    """``exact``, ``other`` or ``neutral`` for a page label against ``release``."""
    if label is None or label in _MOVING:
        return "neutral"
    norm = label.lstrip("v").removesuffix(".x")
    if norm == release or release.startswith(norm + ".") and "." in norm:
        return "exact"
    if release.startswith(norm + ".") or norm.startswith(release + "."):
        return "neutral"  # a major-only page for a minor release, or the reverse
    return "other" if re.fullmatch(r"\d+(\.\d+)*", norm) else "neutral"


def adjust(
    candidates: Sequence[Candidate], url_of: Mapping[str, str], release: str | None
) -> list[Candidate]:
    """Candidates with their value moved by the release match."""
    if release is None:
        return list(candidates)
    out = []
    for c in candidates:
        found = version_label(url_of.get(c.document_id, ""))
        kind = match(found[1] if found else None, release)
        factor = {"exact": 1 + BETA, "other": 1 - BETA}.get(kind, 1.0)
        out.append(replace(c, value=c.value * factor) if factor != 1.0 else c)
    return out


def releases_in(candidates: Sequence[Candidate], url_of: Mapping[str, str]) -> int:
    """How many distinct numbered releases the candidates' pages span."""
    labels = set()
    for c in candidates:
        found = version_label(url_of.get(c.document_id, ""))
        if found and found[1] not in _MOVING:
            labels.add(found[1])
    return len(labels)


def version_select(
    url_of: Mapping[str, str], release: str | None
) -> Callable[..., list[Candidate]]:
    """A drop-in for ``select`` that adjusts values before selecting."""
    from app.retrieve.select import select

    def patched(
        candidates: list[Candidate],
        *,
        budget_tokens: int,
        lambda_: float,
        min_value: float,
    ) -> list[Candidate]:
        return select(adjust(candidates, url_of, release), budget_tokens=budget_tokens,
                      lambda_=lambda_, min_value=min_value)  # fmt: skip

    return patched


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real corpus
    import argparse
    import json
    import statistics
    from pathlib import Path

    import bench.runners.ours as ours_module
    from app.config import get_settings
    from app.providers.embeddings.openai import OpenAIEmbeddingProvider
    from app.providers.rerank.jina import JinaRerankProvider
    from bench.anchor_qrels import in_tune_half
    from bench.corpus_store import CorpusStore
    from bench.m10a_baseline import BUDGETS, bench_task
    from bench.provider_cache import CachingEmbeddingProvider, CachingRerankProvider
    from bench.runners.ours import OursRunner
    from bench.section_recall import coverage, units
    from bench.snapshot import DEFAULT_CORPUS_DIR
    from bench.tasks_schema import load_external_tasks

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--half", choices=["A", "B"], default="A")
    args = parser.parse_args(argv)
    out_dir = Path("bench/results/m10a")
    embeddings = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CachingRerankProvider(JinaRerankProvider())
    store = CorpusStore(DEFAULT_CORPUS_DIR)
    tasks = {t.id: t for t in load_external_tasks(Path("bench/tasks/external.yaml"))}
    keys = [
        k
        for k in map(json.loads, (out_dir / "qrels.jsonl").read_text().splitlines())
        if in_tune_half(k["task_id"]) == (args.half == "A")
    ]
    settings = get_settings()
    runner = OursRunner(lambda_=settings.selection_lambda,
                        min_value=settings.min_value,
                        lexical_weight=settings.lexical_weight,
                        embeddings=embeddings, reranker=reranker)  # fmt: skip
    rows: dict[str, dict[int, list[float]]] = {"current": {}, "version": {}}
    subset: list[int] = []
    original = ours_module.select
    try:
        for i, key in enumerate(keys):
            task = tasks[key["task_id"]]
            question = f"{task.question_title}\n\n{task.question}"
            prep = runner.prepare_candidates(bench_task(task), store=store)
            release = named_release(question)
            url_of = prep.url_by_document
            if release and releases_in(prep.candidates, url_of) >= 2:
                subset.append(i)
            su = units(key["section"])
            for name, rel in (("current", None), ("version", release)):
                ours_module.select = version_select(url_of, rel)  # type: ignore[assignment]
                for b in BUDGETS:
                    res = runner.assemble_from_candidates(prep, budget_tokens=b)
                    rows[name].setdefault(b, []).append(coverage(su, res.text))
    finally:
        ours_module.select = original
    lines = [f"# M10a U2: prefer the named release, half {args.half}", "",
             f"Tasks naming a release with candidates from 2+ releases: "
             f"{len(subset)} of {len(keys)}", ""]  # fmt: skip
    if len(subset) < MIN_TASKS:
        lines.append(f"Fewer than {MIN_TASKS}: U2 stops for lack of power.")
    lines += ["| budget | coverage all (cur → var) | coverage subset (cur → var) |",
              "|---|---|---|"]  # fmt: skip
    for b in BUDGETS:
        cur, var = rows["current"][b], rows["version"][b]
        sub_c = [cur[i] for i in subset] or [0.0]
        sub_v = [var[i] for i in subset] or [0.0]
        all_cell = f"{statistics.mean(cur):.3f} → {statistics.mean(var):.3f}"
        sub_cell = f"{statistics.mean(sub_c):.3f} → {statistics.mean(sub_v):.3f}"
        lines.append(f"| {b} | {all_cell} | {sub_cell} |")
    report = "\n".join(lines) + "\n"
    (out_dir / f"version_{args.half}.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"paid calls: embeddings {embeddings.api_calls}, rerank {reranker.api_calls}")


if __name__ == "__main__":
    main()
