"""Aggregate benchmark results into results.csv / report.md / config.json (§10.5).

:func:`aggregate` takes one :class:`ResultRow` per ``(task_id, strategy)`` and
writes a timestamped directory under ``bench/results/`` with three files:

* ``results.csv`` — one row per ``(task_id, strategy)`` with the columns from
  ``m2a-plan.md`` (``latency_ms`` is excluded from the determinism comparison);
* ``report.md`` — per-strategy and per-category tables: success rate with a 95%
  bootstrap confidence interval (``n_boot=1000``, a fixed seed derived from
  ``strategy+category`` for determinism), mean context tokens, cost per task and
  per COMPLETED task (priced from ``bench/prices.yaml``), latency p50/p95,
  citation precision, and INSUFFICIENT rate — plus the count of tasks IGNORED
  because ``validated == False``;
* ``config.json`` — models, strategies, budget, split, ``n_boot``, ``seed``,
  git commit, and resolved package versions.

Tasks with ``validated == False`` are DROPPED before aggregation and their count
is reported (SPEC §10.5 / acceptance criterion (d)). Time columns are isolated
from the determinism comparison because the fake's latency is ~0 and noisy.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from statistics import mean
from typing import Any

#: Bootstrap resampling count for the success-rate CI (SPEC §10.5; see
#: DECISIONS.md — the SPEC does not fix it, so we pin n_boot/seed for determinism).
N_BOOT = 1000

#: Base seed; a per-(strategy, category) seed is derived from it so each cell's
#: bootstrap is deterministic yet independent.
BASE_SEED = 1234567

#: results.csv columns, in order (m2a-plan.md). ``latency_ms`` is the only time
#: column and is excluded from the determinism comparison.
CSV_COLUMNS = (
    "task_id",
    "strategy",
    "category",
    "split",
    "answer_type",
    "context_tokens",
    "input_tokens",
    "output_tokens",
    "correct",
    "citation_correct",
    "is_insufficient",
    "cost_usd",
    "latency_ms",
)

#: Packages whose resolved versions are recorded in config.json for provenance.
_RECORDED_PACKAGES = ("trafilatura", "rank-bm25", "openai", "anthropic", "tiktoken")


@dataclass(frozen=True)
class ResultRow:
    """One graded ``(task_id, strategy)`` outcome feeding the report.

    ``validated`` carries the task's validation flag so :func:`aggregate` can drop
    unvalidated tasks and report how many were ignored (SPEC §10.5). ``cost_usd``
    is pre-computed by the caller from token counts and ``bench/prices.yaml``.
    """

    task_id: str
    strategy: str
    category: str
    split: str
    answer_type: str
    context_tokens: int
    input_tokens: int
    output_tokens: int
    correct: bool
    citation_correct: bool
    is_insufficient: bool
    cost_usd: float
    latency_ms: float
    validated: bool


def model_cost_usd(
    *, input_tokens: int, output_tokens: int, model: str, prices: dict[str, Any]
) -> float:
    """Return the USD cost of a call priced from ``prices`` (``bench/prices.yaml``).

    ``prices`` is the parsed prices file; per-1M-token input/output rates for
    ``model`` are read from its ``models`` block. An unknown model costs 0.0.
    """
    models = prices.get("models", {})
    entry = models.get(model)
    if entry is None:
        return 0.0
    in_rate = float(entry.get("input_usd_per_1m", 0.0))
    out_rate = float(entry.get("output_usd_per_1m", 0.0))
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate


def _seed_for(strategy: str, category: str) -> int:
    """Derive a stable bootstrap seed for a ``(strategy, category)`` cell.

    Uses a sha256 of the names (NOT Python's ``hash``, which is salted per
    process) so the seed — and therefore the bootstrap CI — is identical across
    runs and processes (acceptance criterion (b)).
    """
    digest = hashlib.sha256(f"{strategy}|{category}".encode()).digest()
    return BASE_SEED ^ int.from_bytes(digest[:4], "big")


def _bootstrap_ci(
    successes: list[bool], *, strategy: str, category: str
) -> tuple[float, float]:
    """Return the 95% bootstrap CI of the success rate (deterministic).

    Resamples ``successes`` with replacement ``N_BOOT`` times using a seed derived
    from ``(strategy, category)`` so the interval is reproducible across runs
    (SPEC §10 determinism / acceptance criterion (b)). An empty input yields
    ``(0.0, 0.0)``.
    """
    if not successes:
        return 0.0, 0.0
    rng = random.Random(_seed_for(strategy, category))
    n = len(successes)
    values = [1.0 if ok else 0.0 for ok in successes]
    means: list[float] = []
    for _ in range(N_BOOT):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * (N_BOOT - 1))]
    hi = means[int(0.975 * (N_BOOT - 1))]
    return lo, hi


def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct`` (0..1) nearest-rank percentile of ``values``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = int(pct * (len(ordered) - 1))
    return ordered[rank]


def _rate(flags: list[bool]) -> float:
    """Return the fraction of ``True`` in ``flags`` (0.0 for an empty list)."""
    return (sum(1 for f in flags if f) / len(flags)) if flags else 0.0


@dataclass(frozen=True)
class _Cell:
    """Aggregated metrics for one strategy, optionally within one category."""

    strategy: str
    category: str
    n_tasks: int
    success_rate: float
    ci_low: float
    ci_high: float
    mean_context_tokens: float
    cost_per_task: float
    cost_per_completed_task: float
    latency_p50: float
    latency_p95: float
    citation_precision: float
    insufficient_rate: float


def _summarize(rows: list[ResultRow], *, strategy: str, category: str) -> _Cell:
    """Build a :class:`_Cell` from the rows of one strategy/(category)."""
    corrects = [r.correct for r in rows]
    completed = [r for r in rows if r.correct]
    ci_low, ci_high = _bootstrap_ci(corrects, strategy=strategy, category=category)
    total_cost = sum(r.cost_usd for r in rows)
    return _Cell(
        strategy=strategy,
        category=category,
        n_tasks=len(rows),
        success_rate=_rate(corrects),
        ci_low=ci_low,
        ci_high=ci_high,
        mean_context_tokens=mean([r.context_tokens for r in rows]) if rows else 0.0,
        cost_per_task=(total_cost / len(rows)) if rows else 0.0,
        cost_per_completed_task=(total_cost / len(completed)) if completed else 0.0,
        latency_p50=_percentile([r.latency_ms for r in rows], 0.50),
        latency_p95=_percentile([r.latency_ms for r in rows], 0.95),
        citation_precision=_rate([r.citation_correct for r in rows]),
        insufficient_rate=_rate([r.is_insufficient for r in rows]),
    )


def _git_commit() -> str:
    """Return the current git commit hash, or ``"unknown"`` if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _package_versions() -> dict[str, str]:
    """Return resolved versions of the benchmark packages (provenance)."""
    versions: dict[str, str] = {}
    for name in _RECORDED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unknown"
    return versions


def _write_results_csv(path: Path, rows: list[ResultRow]) -> None:
    """Write ``results.csv`` with one line per ``(task_id, strategy)``.

    Rows are sorted by ``(strategy, task_id)`` so the file is byte-stable across
    runs. Floats are rendered with fixed precision; ``latency_ms`` (the only time
    column) is written but excluded from the determinism comparison by the test.
    """
    rows = sorted(rows, key=lambda r: (r.strategy, r.task_id))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                r.task_id,
                r.strategy,
                r.category,
                r.split,
                r.answer_type,
                r.context_tokens,
                r.input_tokens,
                r.output_tokens,
                int(r.correct),
                int(r.citation_correct),
                int(r.is_insufficient),
                f"{r.cost_usd:.8f}",
                f"{r.latency_ms:.3f}",
            ]
        )
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _format_cell_row(cell: _Cell, *, label: str) -> str:
    """Render one Markdown table row for ``cell`` under ``label``.

    Latency p50/p95 are deliberately OMITTED from ``report.md`` (they live in
    ``results.csv``): with the fake, latency is ~0 and noisy, so keeping it out
    of the Markdown makes the report byte-stable across runs (acceptance
    criterion (b)). Time is reported only in the CSV, where the test ignores it.
    """
    return (
        f"| {label} | {cell.n_tasks} | {cell.success_rate:.3f} "
        f"| [{cell.ci_low:.3f}, {cell.ci_high:.3f}] "
        f"| {cell.mean_context_tokens:.1f} | {cell.cost_per_task:.6f} "
        f"| {cell.cost_per_completed_task:.6f} | {cell.citation_precision:.3f} "
        f"| {cell.insufficient_rate:.3f} |"
    )


_TABLE_HEADER = (
    "| {first} | n | success | CI95 | ctx_tokens | cost/task "
    "| cost/completed | cite_prec | insuff_rate |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
)


def _write_report_md(
    path: Path,
    rows: list[ResultRow],
    *,
    ignored_count: int,
    config: dict[str, Any],
) -> None:
    """Write the human-readable ``report.md`` (byte-stable, no timestamps)."""
    strategies = sorted({r.strategy for r in rows})
    categories = sorted({r.category for r in rows})

    lines: list[str] = ["# Benchmark report", ""]
    lines.append(f"- answerer: {config['models']['answerer']}")
    lines.append(f"- judge: {config['models']['judge']}")
    lines.append(f"- split: {config['split']}")
    lines.append(f"- budget_tokens: {config['budget']}")
    lines.append(f"- tasks ignored (validated:false): {ignored_count}")
    lines.append("")

    lines.append("## By strategy")
    lines.append("")
    lines.append(_TABLE_HEADER.format(first="strategy"))
    for strategy in strategies:
        cell = _summarize(
            [r for r in rows if r.strategy == strategy],
            strategy=strategy,
            category="*",
        )
        lines.append(_format_cell_row(cell, label=strategy))
    lines.append("")

    lines.append("## By strategy x category")
    lines.append("")
    for strategy in strategies:
        lines.append(f"### {strategy}")
        lines.append("")
        lines.append(_TABLE_HEADER.format(first="category"))
        for category in categories:
            cell_rows = [
                r for r in rows if r.strategy == strategy and r.category == category
            ]
            if not cell_rows:
                continue
            cell = _summarize(cell_rows, strategy=strategy, category=category)
            lines.append(_format_cell_row(cell, label=category))
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_config(
    *,
    prices: dict[str, Any],
    strategies: list[str],
    budget: int,
    split: str,
) -> dict[str, Any]:
    """Assemble the ``config.json`` payload (models, params, provenance)."""
    selected = prices.get("selected", {})
    return {
        "models": {
            "answerer": selected.get("answerer", "unknown"),
            "judge": selected.get("judge", "unknown"),
        },
        "strategies": sorted(strategies),
        "budget": budget,
        "split": split,
        "n_boot": N_BOOT,
        "seed": BASE_SEED,
        "git_commit": _git_commit(),
        "package_versions": _package_versions(),
    }


def aggregate(
    rows: list[ResultRow],
    *,
    prices: dict[str, Any],
    budget: int,
    split: str,
    results_root: str | Path,
    timestamp: str | None = None,
) -> Path:
    """Aggregate ``rows`` and write the three output files; return the directory.

    Rows with ``validated == False`` are DROPPED and their count is reported in
    ``report.md`` (SPEC §10.5 / acceptance criterion (d)). The output directory is
    ``<results_root>/<timestamp>``; ``timestamp`` defaults to the current UTC time
    (the only non-deterministic part, excluded from the determinism comparison).
    """
    validated_rows = [r for r in rows if r.validated]
    ignored_count = len(rows) - len(validated_rows)

    strategies = sorted({r.strategy for r in validated_rows})
    config = _build_config(
        prices=prices, strategies=strategies, budget=budget, split=split
    )

    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    out_dir = Path(results_root) / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_results_csv(out_dir / "results.csv", validated_rows)
    _write_report_md(
        out_dir / "report.md",
        validated_rows,
        ignored_count=ignored_count,
        config=config,
    )
    (out_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out_dir
