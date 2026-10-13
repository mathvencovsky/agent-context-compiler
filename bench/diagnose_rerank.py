"""Defect 2 diagnosis — does the Jina reranker help ``ours`` on this corpus?

Stage 2 measured ``ours_no_rerank`` (0.805) ABOVE ``ours`` (0.732) at budget
4000, so this utility asks — WITH DATA, served from the persistent
``bench/.cache/`` and NEVER re-spending Jina/OpenAI — whether that is a bug in
the rerank -> selection wiring or an honest property of the corpus.

The honest rule is baked in: the real-provider path filters to ``split == "dev"``
in code (the test split is never touched here), reuses the disk cache, and
reports how many Jina calls were actually made (expected ~0, because the dev
pairs were cached by the earlier M8 runs). For every dev task it prepares the
selection-invariant pipeline (``prepare_candidates``) ONCE per variant and
compares, over the SAME budget:

* which ``(document_id, ord)`` fragments ``ours`` (rerank on) vs
  ``ours_no_rerank`` (rerank off) select;
* whether the task's ``expected_evidence`` documents reach the prepared
  candidates and SURVIVE into each variant's emitted context;
* the per-task ``value`` the rerank assigns to the evidence candidates vs the
  non-evidence ones (so a reviewer can see whether rerank demotes the evidence).

The comparison LOGIC is pure (:func:`compare_task`) and unit-tested offline with
synthetic candidates; only the driver (:func:`diagnose`) builds the real
disk-cached providers, and it is lazy-imported so no test needs the SDK/key or
the network.
"""

from __future__ import annotations

import argparse
import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.extract.ir import make_document_id
from bench.corpus_store import CorpusStore
from bench.run import DEFAULT_CORPUS_DIR, DEFAULT_RESULTS_ROOT, DEFAULT_TASKS_PATH
from bench.runners.ours import OursRunner, PreparedCandidates
from bench.tasks_schema import Task, load_tasks

if TYPE_CHECKING:
    from app.providers.embeddings.base import EmbeddingProvider
    from app.providers.rerank.base import RerankProvider

#: The budget the Stage 2 calibration used (compare apples to apples).
DEFAULT_BUDGET = 4000

#: Only the dev split is ever diagnosed (the honesty rule — never look at test).
_DEV_SPLIT = "dev"


@dataclass(frozen=True)
class TaskComparison:
    """Per-task rerank-on vs rerank-off evidence/selection comparison (pure).

    All fields are derived locally from the two prepared candidate sets and the
    task's declared evidence — no LLM, no network. ``*_value_mean`` are the mean
    selection ``value`` the rerank-ON variant assigned to the evidence vs the
    non-evidence candidates, so a reviewer can see whether rerank demotes the
    evidence relative to RRF.
    """

    task_id: str
    category: str
    #: Expected-evidence documents that reached the fused/reranked candidates.
    evidence_in_candidates: bool
    #: Expected-evidence documents that survived into each variant's context.
    evidence_in_context_rerank: bool
    evidence_in_context_norerank: bool
    #: Count of selected fragments per variant.
    selected_rerank: int
    selected_norerank: int
    #: Fragments selected by exactly one variant (selection disagreement size).
    only_rerank: int
    only_norerank: int
    #: Mean rerank-ON ``value`` for evidence vs non-evidence candidates.
    evidence_value_mean: float | None
    other_value_mean: float | None


def _context_document_ids(
    runner: OursRunner, prepared: PreparedCandidates, *, budget: int
) -> set[str]:
    """Document ids that contributed to the runner's emitted context."""
    context = runner.assemble_from_candidates(prepared, budget_tokens=budget)
    return {make_document_id(url) for url in context.used_urls}


def _selected_fragment_keys(
    runner: OursRunner, prepared: PreparedCandidates, *, budget: int
) -> set[tuple[str, int]]:
    """The exact ``(document_id, ord)`` fragments the runner selects.

    Mirrors ``assemble_from_candidates`` selection WITHOUT re-emitting text, so
    the two variants can be compared fragment-by-fragment. Pure and offline.
    """
    from app.retrieve.select import select

    if not prepared.candidates:
        return set()
    if runner.selection == "truncate":  # pragma: no cover - diagnosis uses greedy
        from bench.runners.ours import _truncate_select

        selected = _truncate_select(prepared.candidates, budget_tokens=budget)
    else:
        selected = select(
            prepared.candidates,
            budget_tokens=budget,
            lambda_=runner.lambda_,
            min_value=runner.min_value,
        )
    return {(c.document_id, c.ord) for c in selected}


def compare_task(
    task: Task,
    prepared_rerank: PreparedCandidates,
    prepared_norerank: PreparedCandidates,
    rerank_runner: OursRunner,
    norerank_runner: OursRunner,
    *,
    budget: int,
) -> TaskComparison:
    """Compare the rerank-on vs rerank-off selection for one task (pure).

    Takes the two prepared candidate sets (same retrieval, differing only in the
    rerank flag) and the two runners that produced them, and returns a
    :class:`TaskComparison`. No I/O: every signal is computed from the candidate
    values and the task's declared evidence.
    """
    evidence_ids = {make_document_id(ev.url) for ev in task.expected_evidence}
    candidate_docs = {c.document_id for c in prepared_rerank.candidates}

    ctx_docs_rerank = _context_document_ids(
        rerank_runner, prepared_rerank, budget=budget
    )
    ctx_docs_norerank = _context_document_ids(
        norerank_runner, prepared_norerank, budget=budget
    )

    keys_rerank = _selected_fragment_keys(rerank_runner, prepared_rerank, budget=budget)
    keys_norerank = _selected_fragment_keys(
        norerank_runner, prepared_norerank, budget=budget
    )

    evidence_values = [
        c.value for c in prepared_rerank.candidates if c.document_id in evidence_ids
    ]
    other_values = [
        c.value for c in prepared_rerank.candidates if c.document_id not in evidence_ids
    ]

    return TaskComparison(
        task_id=task.id,
        category=task.category,
        evidence_in_candidates=bool(evidence_ids & candidate_docs),
        evidence_in_context_rerank=bool(evidence_ids & ctx_docs_rerank),
        evidence_in_context_norerank=bool(evidence_ids & ctx_docs_norerank),
        selected_rerank=len(keys_rerank),
        selected_norerank=len(keys_norerank),
        only_rerank=len(keys_rerank - keys_norerank),
        only_norerank=len(keys_norerank - keys_rerank),
        evidence_value_mean=(
            sum(evidence_values) / len(evidence_values) if evidence_values else None
        ),
        other_value_mean=(
            sum(other_values) / len(other_values) if other_values else None
        ),
    )


_CSV_COLUMNS: tuple[str, ...] = (
    "task_id",
    "category",
    "evidence_in_candidates",
    "evidence_in_context_rerank",
    "evidence_in_context_norerank",
    "selected_rerank",
    "selected_norerank",
    "only_rerank",
    "only_norerank",
    "evidence_value_mean",
    "other_value_mean",
)


def _fmt(value: float | None) -> str:
    """Format an optional float for the CSV (empty cell when ``None``)."""
    return "" if value is None else f"{value:.6f}"


def write_comparison_csv(path: Path, rows: Sequence[TaskComparison]) -> str:
    """Serialize the comparisons to CSV text and write it to ``path``.

    Returns the CSV text (so a test can assert on it without reading the file).
    Rows are sorted by ``task_id`` for a byte-stable artifact.
    """
    ordered = sorted(rows, key=lambda r: r.task_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    for r in ordered:
        writer.writerow(
            [
                r.task_id,
                r.category,
                int(r.evidence_in_candidates),
                int(r.evidence_in_context_rerank),
                int(r.evidence_in_context_norerank),
                r.selected_rerank,
                r.selected_norerank,
                r.only_rerank,
                r.only_norerank,
                _fmt(r.evidence_value_mean),
                _fmt(r.other_value_mean),
            ]
        )
    text = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def summarize(rows: Sequence[TaskComparison]) -> dict[str, int]:
    """Aggregate the comparisons into the headline counts for the summary.

    Counts tasks where evidence reached candidates, where it survived into each
    variant's context, and where the two variants disagreed on selection. Pure.
    """
    return {
        "tasks": len(rows),
        "evidence_in_candidates": sum(1 for r in rows if r.evidence_in_candidates),
        "evidence_in_context_rerank": sum(
            1 for r in rows if r.evidence_in_context_rerank
        ),
        "evidence_in_context_norerank": sum(
            1 for r in rows if r.evidence_in_context_norerank
        ),
        "evidence_lost_by_rerank_only": sum(
            1
            for r in rows
            if r.evidence_in_context_norerank and not r.evidence_in_context_rerank
        ),
        "evidence_lost_by_norerank_only": sum(
            1
            for r in rows
            if r.evidence_in_context_rerank and not r.evidence_in_context_norerank
        ),
        "selection_disagreements": sum(
            1 for r in rows if r.only_rerank or r.only_norerank
        ),
    }


def _render_summary_md(
    summary: dict[str, int],
    *,
    budget: int,
    rerank_api_calls: int,
    rerank_cache_hits: int,
) -> str:
    """Render the human-readable ``summary.md`` for the diagnosis artifact."""
    lines = [
        "# Defect 2 diagnosis — rerank vs no-rerank (dev split)",
        "",
        f"- budget: {budget}",
        f"- rerank_api_calls (real Jina): {rerank_api_calls}",
        f"- rerank_cache_hits: {rerank_cache_hits}",
        "",
        "## Headline counts",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)


def _make_retrieval_providers() -> tuple[EmbeddingProvider, RerankProvider]:
    """Build the REAL OpenAI embeddings + Jina reranker, DISK-CACHED (human-only).

    Lazy-imported so no test ever needs the SDK/key; the cache wrappers serve the
    dev pairs computed by the earlier M8 runs, so ``api_calls`` should be ~0.
    """
    from app.providers.embeddings.openai import (
        OpenAIEmbeddingProvider,
    )  # pragma: no cover
    from app.providers.rerank.jina import JinaRerankProvider  # pragma: no cover
    from bench.provider_cache import (  # pragma: no cover
        CachingEmbeddingProvider,
        CachingRerankProvider,
    )

    return (  # pragma: no cover
        CachingEmbeddingProvider(OpenAIEmbeddingProvider()),
        CachingRerankProvider(JinaRerankProvider()),
    )


def diagnose(
    *,
    split: str = _DEV_SPLIT,
    budget: int = DEFAULT_BUDGET,
    corpus_dir: str | Path = DEFAULT_CORPUS_DIR,
    tasks_path: str | Path = DEFAULT_TASKS_PATH,
    results_root: str | Path = DEFAULT_RESULTS_ROOT,
) -> Path:  # pragma: no cover - human-run, builds real providers
    """Run the dev-only rerank diagnosis and write the artifact. Human-run.

    HONESTY GUARD: refuses any split other than ``dev`` so the diagnosis never
    reads the test split. Builds the disk-cached real providers, prepares each
    dev task with rerank on and off over the SAME retrieval, compares the two,
    and writes ``rerank_vs_norerank.csv`` + ``summary.md`` under a timestamped
    directory. Prints how many real Jina calls were made (expected ~0).
    """
    if split != _DEV_SPLIT:
        raise ValueError(
            f"diagnosis is dev-only (honesty rule); refusing split={split!r}"
        )

    from app.config import get_settings

    settings = get_settings()
    store = CorpusStore(corpus_dir)
    tasks = [t for t in load_tasks(tasks_path) if t.split == _DEV_SPLIT]
    if not tasks:
        raise ValueError(f"no dev tasks in {tasks_path}")

    embeddings, reranker = _make_retrieval_providers()
    rerank_runner = OursRunner(
        embeddings=embeddings,
        reranker=reranker,
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
    )
    norerank_runner = OursRunner(
        name="ours_no_rerank",
        use_rerank=False,
        embeddings=embeddings,
        reranker=reranker,
        lambda_=settings.selection_lambda,
        min_value=settings.min_value,
    )

    rows: list[TaskComparison] = []
    for task in tasks:
        prepared_rerank = rerank_runner.prepare_candidates(task, store=store)
        prepared_norerank = norerank_runner.prepare_candidates(task, store=store)
        rows.append(
            compare_task(
                task,
                prepared_rerank,
                prepared_norerank,
                rerank_runner,
                norerank_runner,
                budget=budget,
            )
        )

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(results_root) / f"diagnose_rerank_{ts}"
    write_comparison_csv(out_dir / "rerank_vs_norerank.csv", rows)
    summary = summarize(rows)
    rerank_api_calls = int(getattr(reranker, "api_calls", 0))
    rerank_cache_hits = int(getattr(reranker, "hits", 0))
    (out_dir / "summary.md").write_text(
        _render_summary_md(
            summary,
            budget=budget,
            rerank_api_calls=rerank_api_calls,
            rerank_cache_hits=rerank_cache_hits,
        ),
        encoding="utf-8",
    )
    print(f"rerank_api_calls={rerank_api_calls} rerank_cache_hits={rerank_cache_hits}")
    print(f"wrote rerank diagnosis to {out_dir}")
    return out_dir


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the diagnosis CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m bench.diagnose_rerank",
        description=(
            "Defect 2 diagnosis: compare rerank on/off selection on the DEV "
            "split, served from bench/.cache/ (no re-spend)."
        ),
    )
    parser.add_argument("--split", default=_DEV_SPLIT, choices=[_DEV_SPLIT])
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - human-run
    """CLI entry point: run the dev-only diagnosis and print the output dir."""
    args = _parse_args(argv)
    diagnose(split=args.split, budget=args.budget)


if __name__ == "__main__":  # pragma: no cover
    main()
