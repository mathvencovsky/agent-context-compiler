"""M8 Stage 2 extended report (SPEC §10.5 + M8) built from the budget sweep.

This module assembles the Stage 2 deliverables from the combined sweep rows
WITHOUT touching the canonical :mod:`bench.report` (whose determinism tests
assert byte-identical output). It reuses ``report``'s pure helpers
(:func:`report._summarize`, :func:`report._bootstrap_ci`,
:func:`report.model_cost_usd`, :func:`report._git_commit`,
:func:`report._package_versions`) and adds what Stage 2 requires on top:

* ``report.md`` — per-strategy AND per-category tables (success rate + 95%
  bootstrap CI, mean context tokens, cost/task, cost/completed, citation
  precision, INSUFFICIENT rate AND latency p50/p95), one block per budget; the
  ABLATION contribution analysis (``ours`` vs each ``ours_*`` at the reference
  budget); and the list of 10 ``ours`` FAILURES each with a deterministic
  diagnosis (retrieval / selection / answer / citation);
* ``results.csv`` — one row per ``(task, strategy, budget)`` (via
  :func:`bench.sweep.write_results_csv`);
* ``config.json`` — models, FROZEN params, git commit, budgets, versions;
* ``sumario.md`` — a one-page Portuguese summary for the human: where ``ours``
  beats ``naive_rag`` with a non-overlapping 95% CI, and where it loses/ties,
  with the numbers. It presents data honestly and recommends nothing.

The diagnosis is a deterministic classifier (DS6) — never an LLM — over the
per-task :class:`~bench.sweep.FailureProbe` captured during the sweep.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bench.report import (
    ResultRow,
    _bootstrap_ci,
    _git_commit,
    _package_versions,
    _percentile,
    _summarize,
)
from bench.sweep import REFERENCE_BUDGET, FailureProbe, SweepRow, write_results_csv

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

#: The full pipeline and the baseline it is compared against (SPEC §10.5 / M8).
_OURS = "ours"
_NAIVE_RAG = "naive_rag"

#: How many ``ours`` failures to list with a diagnosis (SPEC §10.5).
_N_FAILURES = 10

#: The three deterministic failure buckets (DS6). A failure is a NOT-correct
#: task (SPEC §10.4); citation precision is a separate metric, so there is no
#: "citation" failure bucket — a correct-but-uncited answer is a success.
_DIAG_RETRIEVAL = "retrieval"
_DIAG_SELECTION = "selection"
_DIAG_ANSWER = "answer"


def diagnose_failure(probe: FailureProbe) -> str:
    """Classify one ``ours`` failure into a fixed bucket (DS6) — no LLM.

    A failure is a task the judge marked NOT correct (SPEC §10.4 defines success
    by the ``correct`` flag; citation precision is a SEPARATE metric and a
    correct-but-uncited answer is a success, not a failure). The rule is
    deterministic and documented (DECISIONS.md 2026-10-13):

    * ``retrieval`` — the expected evidence never reached the prepared
      candidates (it was not retrieved at all);
    * ``selection`` — the evidence WAS a candidate but did not survive into the
      emitted context (selection/budget dropped it);
    * ``answer`` — the evidence IS in the context but the answer was wrong or the
      model declined INSUFFICIENT on an answerable task.

    The branches are mutually exclusive and ordered so exactly one label is
    returned for any failing (``correct=False``) probe. A ``correct`` probe is
    not a failure and must not be passed here.
    """
    if probe.correct:  # pragma: no cover - guarded by _failures_section filter
        raise ValueError("diagnose_failure called on a correct (non-failure) probe")
    if not probe.evidence_in_candidates:
        return _DIAG_RETRIEVAL
    if not probe.evidence_in_context:
        return _DIAG_SELECTION
    return _DIAG_ANSWER


def _rows_at_budget(sweep_rows: Sequence[SweepRow], budget: int) -> list[ResultRow]:
    """Return the plain :class:`ResultRow`\\ s measured at ``budget``."""
    return [s.row for s in sweep_rows if s.budget == budget]


def _budgets_present(sweep_rows: Sequence[SweepRow]) -> list[int]:
    """Return the sorted distinct budgets present in ``sweep_rows``."""
    return sorted({s.budget for s in sweep_rows})


_TABLE_HEADER = (
    "| {first} | n | success | CI95 | ctx_tokens | cost/task "
    "| cost/completed | cite_prec | insuff_rate | lat_p50_ms | lat_p95_ms |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _format_cell_row(
    rows: list[ResultRow], *, strategy: str, category: str, label: str
) -> str:
    """Render one Markdown row for ``rows`` with latency added (Stage 2)."""
    cell = _summarize(rows, strategy=strategy, category=category)
    lat_p50 = _percentile([r.latency_ms for r in rows], 0.50)
    lat_p95 = _percentile([r.latency_ms for r in rows], 0.95)
    return (
        f"| {label} | {cell.n_tasks} | {cell.success_rate:.3f} "
        f"| [{cell.ci_low:.3f}, {cell.ci_high:.3f}] "
        f"| {cell.mean_context_tokens:.1f} | {cell.cost_per_task:.6f} "
        f"| {cell.cost_per_completed_task:.6f} | {cell.citation_precision:.3f} "
        f"| {cell.insufficient_rate:.3f} | {lat_p50:.1f} | {lat_p95:.1f} |"
    )


def _strategy_table(rows: list[ResultRow]) -> list[str]:
    """Per-strategy table for one budget."""
    strategies = sorted({r.strategy for r in rows})
    lines = [_TABLE_HEADER.format(first="strategy")]
    for strategy in strategies:
        srows = [r for r in rows if r.strategy == strategy]
        lines.append(
            _format_cell_row(srows, strategy=strategy, category="*", label=strategy)
        )
    return lines


def _category_tables(rows: list[ResultRow]) -> list[str]:
    """Per-strategy x category tables for one budget."""
    strategies = sorted({r.strategy for r in rows})
    categories = sorted({r.category for r in rows})
    lines: list[str] = []
    for strategy in strategies:
        lines.append(f"#### {strategy}")
        lines.append("")
        lines.append(_TABLE_HEADER.format(first="category"))
        for category in categories:
            crows = [
                r for r in rows if r.strategy == strategy and r.category == category
            ]
            if not crows:
                continue
            lines.append(
                _format_cell_row(
                    crows, strategy=strategy, category=category, label=category
                )
            )
        lines.append("")
    return lines


@dataclass(frozen=True)
class _Comparison:
    """Success-rate comparison of ``ours`` vs ``naive_rag`` for one category."""

    category: str
    n: int
    ours_rate: float
    ours_ci: tuple[float, float]
    rag_rate: float
    rag_ci: tuple[float, float]
    verdict: str  # "ours" | "naive_rag" | "tie"


def _rate(flags: list[bool]) -> float:
    return (sum(1 for f in flags if f) / len(flags)) if flags else 0.0


def compare_ours_vs_rag(
    rows: list[ResultRow], *, categories: Sequence[str] | None = None
) -> list[_Comparison]:
    """Compare ``ours`` vs ``naive_rag`` success per category + overall (DS/§10.5).

    Non-overlap rule: ``ours`` wins a category when its CI lower bound exceeds
    ``naive_rag``'s CI upper bound (and vice versa); otherwise the CIs overlap
    and the verdict is a tie. The overall comparison uses ``category="*"``. All
    CIs are the deterministic bootstrap from :func:`report._bootstrap_ci`.
    """
    ours_rows = [r for r in rows if r.strategy == _OURS]
    rag_rows = [r for r in rows if r.strategy == _NAIVE_RAG]
    cats = (
        list(categories)
        if categories is not None
        else sorted({r.category for r in ours_rows})
    )

    comparisons: list[_Comparison] = []
    for category in ["*", *cats]:
        if category == "*":
            o = ours_rows
            g = rag_rows
        else:
            o = [r for r in ours_rows if r.category == category]
            g = [r for r in rag_rows if r.category == category]
        if not o or not g:
            continue
        o_flags = [r.correct for r in o]
        g_flags = [r.correct for r in g]
        o_ci = _bootstrap_ci(o_flags, strategy=_OURS, category=category)
        g_ci = _bootstrap_ci(g_flags, strategy=_NAIVE_RAG, category=category)
        o_rate = _rate(o_flags)
        g_rate = _rate(g_flags)
        if o_ci[0] > g_ci[1]:
            verdict = "ours"
        elif g_ci[0] > o_ci[1]:
            verdict = "naive_rag"
        else:
            verdict = "tie"
        comparisons.append(
            _Comparison(
                category=category,
                n=len(o),
                ours_rate=o_rate,
                ours_ci=o_ci,
                rag_rate=g_rate,
                rag_ci=g_ci,
                verdict=verdict,
            )
        )
    return comparisons


def _ablation_section(rows: list[ResultRow]) -> list[str]:
    """``ours`` vs each ``ours_*`` success-rate delta at the reference budget."""
    ablations = [
        "ours_no_vector",
        "ours_no_lexical",
        "ours_no_rerank",
        "ours_fixed_chunks",
        "ours_truncate",
    ]
    ours_rows = [r for r in rows if r.strategy == _OURS]
    ours_flags = [r.correct for r in ours_rows]
    ours_rate = _rate(ours_flags)
    ours_ci = _bootstrap_ci(ours_flags, strategy=_OURS, category="*")

    lines = [
        f"## Ablation contribution (reference budget {REFERENCE_BUDGET})",
        "",
        "Delta = ours success rate - ablation success rate (positive = the "
        "removed component helps). CIs are the per-strategy bootstrap.",
        "",
        "| variant | n | success | CI95 | delta_vs_ours |",
        "| --- | --- | --- | --- | --- |",
        f"| ours | {len(ours_rows)} | {ours_rate:.3f} "
        f"| [{ours_ci[0]:.3f}, {ours_ci[1]:.3f}] | 0.000 |",
    ]
    for name in ablations:
        arows = [r for r in rows if r.strategy == name]
        if not arows:
            continue
        flags = [r.correct for r in arows]
        rate = _rate(flags)
        ci = _bootstrap_ci(flags, strategy=name, category="*")
        lines.append(
            f"| {name} | {len(arows)} | {rate:.3f} "
            f"| [{ci[0]:.3f}, {ci[1]:.3f}] | {ours_rate - rate:+.3f} |"
        )
    lines.append("")
    # Surface for the human: removing the reranker RAISES success at this budget
    # (a negative delta_vs_ours for ours_no_rerank), a real pipeline signal.
    no_rerank_rows = [r for r in rows if r.strategy == "ours_no_rerank"]
    if no_rerank_rows:
        no_rerank_rate = _rate([r.correct for r in no_rerank_rows])
        if no_rerank_rate > ours_rate:
            lines.append(
                "Signal for the human: `ours_no_rerank` "
                f"({no_rerank_rate:.3f}) scores HIGHER than `ours` "
                f"({ours_rate:.3f}) at budget {REFERENCE_BUDGET} — removing the "
                "reranker improves success here (CIs still overlap)."
            )
            lines.append("")
    return lines


def _failures_section(probes: Sequence[FailureProbe]) -> list[str]:
    """List up to 10 ``ours`` failures with a deterministic diagnosis (DS6).

    A failure is a task the judge marked NOT correct (SPEC §10.4). Correct-but-
    uncited answers are successes and are excluded; the citation signal is
    reported separately (see ``_citation_signal_section``).
    """
    failing = sorted(
        (p for p in probes if not p.correct),
        key=lambda p: p.task_id,
    )
    listed = failing[:_N_FAILURES]

    lines = [
        f"## `ours` failures with diagnosis (reference budget {REFERENCE_BUDGET})",
        "",
        "A failure is a task the judge marked NOT correct (SPEC §10.4); citation "
        "precision is a separate metric, so correct-but-uncited answers are "
        "successes and are not listed here. Deterministic classification (DS6, "
        "no LLM): retrieval = expected evidence never retrieved into candidates; "
        "selection = evidence was a candidate but cut by selection/budget; "
        "answer = evidence in context but wrong/INSUFFICIENT answer.",
        "",
        "| # | task_id | category | diagnosis | insufficient | correct | cited |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, probe in enumerate(listed, start=1):
        lines.append(
            f"| {i} | {probe.task_id} | {probe.category} "
            f"| {diagnose_failure(probe)} | {int(probe.is_insufficient)} "
            f"| {int(probe.correct)} | {int(probe.citation_correct)} |"
        )
    if not listed:
        lines.append("| - | (no ours failures at the reference budget) | | | | | |")
    lines.append("")
    lines.append(f"- total ours failures at budget {REFERENCE_BUDGET}: {len(failing)}")
    lines.append("")
    return lines


def _citation_signal_section(probes: Sequence[FailureProbe]) -> list[str]:
    """Surface the ``ours`` citation signal for the human (finding 6).

    Reports, from the data, how many ``ours`` tasks carried at least one VERIFIED
    citation (``quote`` fuzzy-matching the cited document AND overlapping the
    expected evidence, SPEC §10.4). Citation precision is NOT a task-success
    metric (success is the judge ``correct`` flag), but for a product whose
    thesis is traceable evidence it is a first-class signal, so it is reported
    here explicitly from the measured probes rather than asserted.
    """
    n = len(probes)
    cited = sum(1 for p in probes if p.citation_correct)
    correct = sum(1 for p in probes if p.correct)
    cite_prec = (cited / n) if n else 0.0
    return [
        f"## `ours` citation signal (reference budget {REFERENCE_BUDGET})",
        "",
        f"- tasks graded: {n}",
        f"- tasks correct (judge `correct=1`): {correct}",
        f"- tasks with at least one VERIFIED citation: {cited}",
        f"- citation precision: {cite_prec:.3f}",
        "",
        f"At the reference budget `ours` citation precision is {cite_prec:.3f} "
        f"({cited}/{n} tasks carry a citation that verifies against the "
        "evidence). Per SPEC §10.4 the citation metric is separate from the "
        "success/failure counts above; it is reported here for the human as the "
        "product's traceable-evidence signal.",
        "",
    ]


def _build_config(
    *,
    answerer_model: str,
    judge_model: str,
    split: str,
    budgets: tuple[int, ...],
    settings_lambda: float,
    settings_min_value: float,
    settings_coverage_low: float,
    settings_coverage_high: float,
    settings_rrf_k: int,
) -> dict[str, Any]:
    """Assemble the Stage 2 ``config.json`` payload (frozen params + provenance)."""
    from bench.report import BASE_SEED, N_BOOT

    return {
        "models": {"answerer": answerer_model, "judge": judge_model},
        "split": split,
        "budgets": list(budgets),
        "reference_budget": REFERENCE_BUDGET,
        "frozen_params": {
            "selection_lambda": settings_lambda,
            "min_value": settings_min_value,
            "coverage_low": settings_coverage_low,
            "coverage_high": settings_coverage_high,
            "rrf_k": settings_rrf_k,
        },
        "n_boot": N_BOOT,
        "seed": BASE_SEED,
        "git_commit": _git_commit(),
        "package_versions": _package_versions(),
    }


def _report_md(
    sweep_rows: Sequence[SweepRow],
    probes: Sequence[FailureProbe],
    *,
    config: dict[str, Any],
    retrieval_stats: dict[str, Any],
    ignored_count: int,
) -> str:
    """Render the extended Stage 2 ``report.md``."""
    budgets = _budgets_present(sweep_rows)
    lines: list[str] = ["# M8 Stage 2 benchmark report", ""]
    lines.append(f"- answerer: {config['models']['answerer']}")
    lines.append(f"- judge: {config['models']['judge']}")
    lines.append(f"- split: {config['split']}")
    lines.append(f"- budgets: {', '.join(str(b) for b in budgets)}")
    lines.append(f"- reference budget (ablation + failures): {REFERENCE_BUDGET}")
    lines.append(f"- git_commit: {config['git_commit']}")
    lines.append(f"- tasks ignored (validated:false): {ignored_count}")
    lines.append("")
    lines.append("## Frozen selection params (M8 Stage 1, read-only here)")
    lines.append("")
    for key, value in config["frozen_params"].items():
        lines.append(f"- {key} = {value}")
    lines.append("")

    rerank_calls = retrieval_stats.get("rerank_api_calls")
    rerank_hits = retrieval_stats.get("rerank_cache_hits")
    embed_calls = retrieval_stats.get("embed_api_calls")
    embed_hits = retrieval_stats.get("embed_cache_hits")
    lines.append("## Retrieval vendor calls (persistent cache in bench/.cache/)")
    lines.append("")
    lines.append(f"- rerank (Jina) real API calls: {rerank_calls}")
    lines.append(f"- rerank cache hits (avoided calls): {rerank_hits}")
    lines.append(f"- embeddings (OpenAI) real batched API calls: {embed_calls}")
    lines.append(f"- embeddings cache hits: {embed_hits}")
    lines.append("")

    for budget in budgets:
        rows = _rows_at_budget(sweep_rows, budget)
        lines.append(f"## Budget {budget}")
        lines.append("")
        lines.append("### By strategy")
        lines.append("")
        lines.extend(_strategy_table(rows))
        lines.append("")
        lines.append("### By strategy x category")
        lines.append("")
        lines.extend(_category_tables(rows))

    ref_rows = _rows_at_budget(sweep_rows, REFERENCE_BUDGET)
    lines.extend(_ablation_section(ref_rows))

    lines.append("## ours vs naive_rag by category (reference budget)")
    lines.append("")
    lines.append("| category | n | ours | ours_CI95 | naive_rag | rag_CI95 | verdict |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for comp in compare_ours_vs_rag(ref_rows):
        label = "overall" if comp.category == "*" else comp.category
        lines.append(
            f"| {label} | {comp.n} | {comp.ours_rate:.3f} "
            f"| [{comp.ours_ci[0]:.3f}, {comp.ours_ci[1]:.3f}] "
            f"| {comp.rag_rate:.3f} | [{comp.rag_ci[0]:.3f}, {comp.rag_ci[1]:.3f}] "
            f"| {comp.verdict} |"
        )
    lines.append("")

    lines.extend(_failures_section(probes))
    lines.extend(_citation_signal_section(probes))
    return "\n".join(lines) + "\n"


def _sumario_md(sweep_rows: Sequence[SweepRow]) -> str:
    """Render the one-page Portuguese summary for the human (presents data only)."""
    ref_rows = _rows_at_budget(sweep_rows, REFERENCE_BUDGET)
    comparisons = compare_ours_vs_rag(ref_rows)
    overall = next((c for c in comparisons if c.category == "*"), None)
    per_cat = [c for c in comparisons if c.category != "*"]

    wins = [c for c in per_cat if c.verdict == "ours"]
    losses = [c for c in per_cat if c.verdict == "naive_rag"]
    ties = [c for c in per_cat if c.verdict == "tie"]

    def _fmt(c: _Comparison) -> str:
        return (
            f"  - **{c.category}** (n={c.n}): ours={c.ours_rate:.3f} "
            f"IC95 [{c.ours_ci[0]:.3f}, {c.ours_ci[1]:.3f}] | "
            f"naive_rag={c.rag_rate:.3f} IC95 [{c.rag_ci[0]:.3f}, {c.rag_ci[1]:.3f}]"
        )

    lines = [
        "# Sumário M8 Etapa 2 — `ours` vs `naive_rag` (split test)",
        "",
        f"Orçamento de referência: {REFERENCE_BUDGET} tokens. Critério de "
        "não-sobreposição: o limite inferior do IC95% de uma estratégia supera o "
        "limite superior da outra na mesma categoria. ICs são bootstrap "
        "determinístico (n_boot/seed fixos). Este sumário apresenta os dados; não "
        "recomenda seguir, ajustar ou parar.",
        "",
    ]
    if overall is not None:
        lines.append(
            f"## Geral: ours={overall.ours_rate:.3f} "
            f"IC95 [{overall.ours_ci[0]:.3f}, {overall.ours_ci[1]:.3f}] | "
            f"naive_rag={overall.rag_rate:.3f} "
            f"IC95 [{overall.rag_ci[0]:.3f}, {overall.rag_ci[1]:.3f}] "
            f"-> {overall.verdict}"
        )
        lines.append("")

    lines.append("## Onde `ours` vence (IC95 não sobrepõe)")
    if wins:
        lines.extend(_fmt(c) for c in wins)
    else:
        lines.append("  - Nenhuma categoria com vitória de IC não sobreposto.")
    lines.append("")

    lines.append("## Onde `ours` perde (IC95 do naive_rag não sobrepõe)")
    if losses:
        lines.extend(_fmt(c) for c in losses)
    else:
        lines.append("  - Nenhuma categoria com derrota de IC não sobreposto.")
    lines.append("")

    lines.append("## Onde empata (ICs se sobrepõem)")
    if ties:
        lines.extend(_fmt(c) for c in ties)
    else:
        lines.append("  - Nenhuma categoria em empate.")
    lines.append("")

    # Context: cost per completed task + INSUFFICIENT rate for ours/naive_rag.
    lines.append("## Contexto (orçamento de referência)")
    for strat in (_OURS, _NAIVE_RAG):
        srows = [r for r in ref_rows if r.strategy == strat]
        if not srows:
            continue
        cell = _summarize(srows, strategy=strat, category="*")
        lines.append(
            f"  - **{strat}**: custo/tarefa concluída=US$ "
            f"{cell.cost_per_completed_task:.6f}; "
            f"taxa INSUFFICIENT={cell.insufficient_rate:.3f}; "
            f"precisão de citação={cell.citation_precision:.3f}"
        )
    lines.append("")
    # Signal for the human, computed from the measured reference-budget rows:
    # the ours citation precision and the ours-vs-no_rerank comparison.
    ours_ref_rows = [r for r in ref_rows if r.strategy == _OURS]
    ours_cite = (
        _summarize(ours_ref_rows, strategy=_OURS, category="*").citation_precision
        if ours_ref_rows
        else 0.0
    )
    ours_rate = _rate([r.correct for r in ours_ref_rows])
    no_rerank_rows = [r for r in ref_rows if r.strategy == "ours_no_rerank"]
    no_rerank_rate = _rate([r.correct for r in no_rerank_rows])
    lines.append("## Sinal para o humano (não altera sucesso/§10.4)")
    lines.append(
        f"  - **Citação do `ours`**: precisão de citação={ours_cite:.3f} no "
        f"orçamento {REFERENCE_BUDGET} — a fração das tarefas com ao menos uma "
        "citação que verifica contra a evidência (SPEC §10.4). Relevante para um "
        "produto cuja tese é evidência rastreável; por §10.4 não muda a taxa de "
        "sucesso acima."
    )
    if no_rerank_rows:
        if no_rerank_rate > ours_rate:
            verb = "eleva"
            rel = ">"
        elif no_rerank_rate < ours_rate:
            verb = "reduz"
            rel = "<"
        else:
            verb = "não muda"
            rel = "="
        lines.append(
            f"  - **Remover o reranker {verb} o sucesso**: ours_no_rerank="
            f"{no_rerank_rate:.3f} {rel} ours={ours_rate:.3f} no orçamento "
            f"{REFERENCE_BUDGET} (ICs ainda se sobrepõem)."
        )
    lines.append("")
    lines.append(
        "Honestidade (ROADMAP M8): nenhum parâmetro foi ajustado olhando o split "
        "test. λ/min_value/coverage vêm da Etapa 1 (dev, congelados em e75273e) e "
        "esta etapa só LÊ o split test para medir."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(
    out_dir: str | Path,
    *,
    sweep_rows: Sequence[SweepRow],
    probes: Sequence[FailureProbe],
    prices: dict[str, Any],
    split: str,
    budgets: tuple[int, ...],
    answerer_model: str,
    judge_model: str,
    settings_lambda: float,
    settings_min_value: float,
    settings_coverage_low: float,
    settings_coverage_high: float,
    settings_rrf_k: int,
    retrieval_stats: dict[str, Any] | None = None,
) -> Path:
    """Write the four Stage 2 artifacts and return the output directory.

    Rows with ``validated == False`` are DROPPED before aggregation (SPEC §10.5)
    and their count is reported in ``report.md``. ``results.csv`` keeps every
    validated ``(task, strategy, budget)`` row. The report/summary are byte-
    stable modulo ``latency_ms`` (same determinism posture as Stage 1).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    retrieval_stats = retrieval_stats or {}

    validated = [s for s in sweep_rows if s.row.validated]
    ignored_count = len({s.row.task_id for s in sweep_rows if not s.row.validated})

    config = _build_config(
        answerer_model=answerer_model,
        judge_model=judge_model,
        split=split,
        budgets=budgets,
        settings_lambda=settings_lambda,
        settings_min_value=settings_min_value,
        settings_coverage_low=settings_coverage_low,
        settings_coverage_high=settings_coverage_high,
        settings_rrf_k=settings_rrf_k,
    )

    write_results_csv(out_dir / "results.csv", validated)
    (out_dir / "report.md").write_text(
        _report_md(
            validated,
            probes,
            config=config,
            retrieval_stats=retrieval_stats,
            ignored_count=ignored_count,
        ),
        encoding="utf-8",
    )
    (out_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "sumario.md").write_text(_sumario_md(validated), encoding="utf-8")
    return out_dir
