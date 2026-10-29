"""The "full CPT": cost per correct task with the retrieval providers' cost.

Research front F, lesson L3. The §6 CPT prices only the agent's model calls.
``webfetch_live`` carries the cost of the model WebFetch reads pages with
(Amendment 8), but ``nina`` and ``strong_rag`` leave out the OpenAI embeddings
and the Jina rerank their tools call. This module prices those calls, logged
per session by :func:`bench.m10_tools.usage_logged` in
``bench/results/m10/provider_usage/<task>__<arm>__<attempt>.jsonl``, and
reports, as a sensitivity analysis next to §6 (never a decision):

* CPT and full CPT per arm;
* USD per 1,000 tasks and per 1,000 correct tasks, the unit vendors use for
  cost claims;
* R = CPT(full_page) / CPT(nina) recomputed with the full costs, with the §6
  task bootstrap.

The log counts every call as if the bench's provider cache did not exist, so
it is what a customer would pay. Rerank calls are billed as the query once per
document plus the documents, an upper reading of a per-token rerank price.

Prices come from the ``retrieval`` section of ``bench/prices.yaml``. A price
that is not a number (the ``TODO`` placeholder) or is missing raises: the full
CPT is never computed with a silent zero.

Usage::

    uv run python -m bench.full_cpt --out bench/results/m10
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.m10_analysis import N_BOOT, bootstrap, cpt_ratio, percentile
from bench.roi import TaskCost


def _price(model: str, prices: Mapping[str, Any]) -> float:
    entry = prices.get("retrieval", {}).get(model)
    if entry is None:
        raise ValueError(f"no price for retrieval model {model!r} in bench/prices.yaml")
    value = entry.get("usd_per_1m_tokens")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"the price of {model!r} in bench/prices.yaml is {value!r} (TODO): "
            "fill it from the provider's pricing page before using the full CPT"
        )
    return float(value) / 1_000_000


def _billed_tokens(row: Mapping[str, Any]) -> int:
    if row["kind"] == "embed":
        return int(row["tokens"])
    if row["kind"] == "rerank":
        return int(row["documents"]) * int(row["query_tokens"]) + int(
            row["document_tokens"]
        )
    raise ValueError(f"unknown provider call kind {row['kind']!r}")


def retrieval_usd(
    rows: Sequence[Mapping[str, Any]], prices: Mapping[str, Any]
) -> float:
    """USD of the logged provider calls."""
    return sum(_billed_tokens(r) * _price(str(r["model"]), prices) for r in rows)


def session_retrieval_usd(path: Path, prices: Mapping[str, Any]) -> float:
    """USD of one session's usage file; 0 when the session logged nothing."""
    if not path.exists():
        return 0.0
    lines = path.read_text(encoding="utf-8").splitlines()
    return retrieval_usd([json.loads(line) for line in lines if line.strip()], prices)


def usage_by_session(
    folder: Path, prices: Mapping[str, Any]
) -> dict[tuple[str, str], float]:
    """(task, arm) -> retrieval USD of the last attempt, as §6 costs a session."""
    last: dict[tuple[str, str], tuple[int, Path]] = {}
    for path in folder.glob("*.jsonl"):
        task, arm, attempt = path.stem.rsplit("__", 2)
        if (task, arm) not in last or int(attempt) > last[(task, arm)][0]:
            last[(task, arm)] = (int(attempt), path)
    return {k: session_retrieval_usd(p, prices) for k, (_n, p) in last.items()}


@dataclass(frozen=True)
class ArmCost:
    """One arm's totals over the primary population."""

    n: int
    correct: int
    cost_usd: float
    retrieval_usd: float

    @property
    def cpt(self) -> float:
        return self.cost_usd / self.correct if self.correct else float("inf")

    @property
    def full_cpt(self) -> float:
        total = self.cost_usd + self.retrieval_usd
        return total / self.correct if self.correct else float("inf")

    @property
    def usd_per_1000_tasks(self) -> float:
        return 1000 * self.cost_usd / self.n

    @property
    def full_usd_per_1000_tasks(self) -> float:
        return 1000 * (self.cost_usd + self.retrieval_usd) / self.n

    @property
    def full_usd_per_1000_correct(self) -> float:
        return 1000 * self.full_cpt


def full_ratio(
    full: Sequence[TaskCost],
    nina: Sequence[TaskCost],
    full_retrieval: Sequence[float],
    nina_retrieval: Sequence[float],
    *,
    n_boot: int = N_BOOT,
) -> tuple[float, float]:
    """R with full costs and its §6 lower bound (2.5th percentile)."""
    if not (len(full) == len(nina) == len(full_retrieval) == len(nina_retrieval)):
        raise ValueError("one retrieval cost per task and arm")

    def plus(tasks: Sequence[TaskCost], extra: Sequence[float]) -> list[TaskCost]:
        return [
            TaskCost(
                cost_usd=t.cost_usd + x, tool_tokens=t.tool_tokens, correct=t.correct
            )
            for t, x in zip(tasks, extra, strict=True)
        ]

    f, m = plus(full, full_retrieval), plus(nina, nina_retrieval)
    values = bootstrap(f, m, cpt_ratio, n_boot=n_boot)
    return cpt_ratio(f, m), percentile(values, 0.025)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - run output
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("bench/results/m10"))
    parser.add_argument("--prices", type=Path, default=Path("bench/prices.yaml"))
    args = parser.parse_args(argv)

    prices = yaml.safe_load(args.prices.read_text(encoding="utf-8"))
    usd = usage_by_session(args.out / "provider_usage", prices)
    with (args.out / "results.csv").open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["correct"] != ""]
    by: dict[str, dict[str, dict[str, str]]] = {}
    for r in rows:
        if r["split"] == "holdout":
            by.setdefault(r["strategy"], {})[r["task_id"]] = r
    tasks = sorted(set(by.get("nina", {})) & set(by.get("full_page", {})))
    print("| arm | n | CPT | full CPT | USD/1k tasks | full USD/1k tasks | "
          "full USD/1k correct |")  # fmt: skip
    print("|---|---|---|---|---|---|---|")
    for arm in sorted(by):
        mine = [by[arm][t] for t in tasks if t in by[arm]]
        if not mine:
            continue
        c = ArmCost(
            n=len(mine),
            correct=sum(r["correct"] == "1" for r in mine),
            cost_usd=sum(float(r["cost_usd"]) for r in mine),
            retrieval_usd=sum(usd.get((r["task_id"], arm), 0.0) for r in mine),
        )
        print(
            f"| {arm} | {c.n} | {c.cpt:.4f} | {c.full_cpt:.4f} | "
            f"{c.usd_per_1000_tasks:.2f} | {c.full_usd_per_1000_tasks:.2f} | "
            f"{c.full_usd_per_1000_correct:.2f} |"
        )

    def costs(arm: str) -> list[TaskCost]:
        return [
            TaskCost(
                cost_usd=float(by[arm][t]["cost_usd"]),
                tool_tokens=int(by[arm][t]["tool_result_tokens"]),
                correct=by[arm][t]["correct"] == "1",
            )
            for t in tasks
        ]

    ratio, lo = full_ratio(
        costs("full_page"),
        costs("nina"),
        [usd.get((t, "full_page"), 0.0) for t in tasks],
        [usd.get((t, "nina"), 0.0) for t in tasks],
    )
    print(
        f"\nR with full costs: {ratio:.3f} (lower 95% bound {lo:.3f}); sensitivity only"
    )


if __name__ == "__main__":
    main()
